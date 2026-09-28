# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-4da818b41ccadee204345352

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-4da818b41ccadee204345352"


import os
import sys
import re
import json
import sqlite3
import subprocess
import shutil
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from shared.platform_runtime import get_config_dir, get_service_data_dir, is_compiled
from shared.secure_storage import SecureStorageError, append_secure_file, read_secure_file, write_secure_file

# Ensure autoyou-outreach path is in sys.path so we can import outreach modules if needed
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTREACH_ROOT = PROJECT_ROOT / "autoyou-outreach"
if OUTREACH_ROOT.exists() and str(OUTREACH_ROOT) not in sys.path:
    sys.path.insert(0, str(OUTREACH_ROOT))

MEDIA_DATA_DIR = get_service_data_dir("media_generation_agent", anchor=__file__)
DB_PATH = MEDIA_DATA_DIR / "media_history.db"
OUTPUT_DIR = MEDIA_DATA_DIR / "output"

VALID_MEDIA_TYPES = {"video", "image"}
IMAGE_OUTPUT_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_OUTPUT_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv")
PROMPT_MAX_CHARS = 1200
DEFAULT_IMAGE_MODEL_CANDIDATES = (
    "flux_schnell",
    "flux",
    "z_image",
    "qwen_image_20B",
    "qwen_image_2512_20B",
)
MODEL_TYPE_ALIASES = {
    "qwen_image_20b": "qwen_image_20B",
    "qwen_image_2512_20b": "qwen_image_2512_20B",
    "wan2.1_text2video_14b": "t2v",
    "wan2.2_text2video_14b": "t2v_2_2",
}
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\r\n\"'<>]+")
_POSIX_PRIVATE_PATH_RE = re.compile(r"(?<!\w)/(?:Users|home|var/folders|private/tmp|tmp)/[^\s\"'<>]+")
_LOCAL_PATH_PLACEHOLDER = "[local path]"
_EXTERNAL_PYTHON_ENV_DROP_KEYS = {
    "AUTOYOU_PACKAGED_RUNTIME",
    "AUTOYOU_PACKAGED_RESOURCES_ROOT",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONPLATLIBDIR",
    "PYTHONEXECUTABLE",
    "PYTHONUSERBASE",
    "VIRTUAL_ENV",
    "__PYVENV_LAUNCHER__",
}

def redact_local_paths_for_display(value: Any) -> Any:
    """Redact absolute local filesystem paths before sending text to remote clients."""
    if not isinstance(value, str):
        return value
    text = _WINDOWS_ABSOLUTE_PATH_RE.sub(_LOCAL_PATH_PLACEHOLDER, value)
    text = _POSIX_PRIVATE_PATH_RE.sub(_LOCAL_PATH_PLACEHOLDER, text)
    return text

def sanitize_media_history_item_for_api(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return a history row safe for browser/chat display."""
    public_item = dict(item or {})
    redacted = False
    file_path = str(public_item.get("file_path") or "").strip()
    if file_path:
        file_name = str(public_item.get("file_name") or "").strip() or Path(file_path).name
        public_item["file_path"] = f"[local media file]/{file_name}" if file_name else "[local media file]"
        redacted = True
    for key in ("error_message",):
        original = public_item.get(key)
        sanitized = redact_local_paths_for_display(original)
        if sanitized != original:
            public_item[key] = sanitized
            redacted = True
    if redacted:
        public_item["local_paths_redacted"] = True
    return public_item

def _external_python_subprocess_env() -> Dict[str, str]:
    """Build a clean environment for external venv Python processes.

    Packaged AutoYou sets Python runtime variables for its own embedded
    interpreter. Letting those leak into Pinokio/Wan2GP makes the external venv
    import AutoYou's bundled stdlib and crash before wgp.py starts.
    """
    env = os.environ.copy()
    for key in _EXTERNAL_PYTHON_ENV_DROP_KEYS:
        env.pop(key, None)
    return env

def init_db() -> None:
    """Initialize history SQLite database and execute migration schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                media_type TEXT NOT NULL,
                original_prompt TEXT NOT NULL,
                optimized_prompt TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                model_type TEXT,
                resolution TEXT,
                steps INTEGER,
                frames INTEGER,
                seed INTEGER,
                status TEXT NOT NULL,
                error_message TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()

# Initialize immediately when this module is loaded
init_db()

MANAGED_WAN2GP_ROOT = MEDIA_DATA_DIR / "wan2gp"
MANAGED_WAN2GP_APP_DIR = MANAGED_WAN2GP_ROOT / "app"
WAN2GP_GIT_URL = "https://github.com/deepbeepmeep/Wan2GP.git"

def _wan2gp_python_relpaths() -> Tuple[str, ...]:
    """Relative virtualenv python locations inside a Wan2GP app folder."""
    if os.name == "nt":
        return (
            "env/Scripts/python.exe",
            "venv/Scripts/python.exe",
            ".venv/Scripts/python.exe",
        )
    return ("env/bin/python", "venv/bin/python", ".venv/bin/python")

def _candidate_pinokio_homes() -> List[Path]:
    """Likely Pinokio home folders for the current machine."""
    homes: List[Path] = []
    env_home = str(os.getenv("PINOKIO_HOME") or "").strip()
    if env_home:
        homes.append(Path(env_home).expanduser())
    user_home = Path.home()
    homes.extend([user_home / "pinokio", user_home / "Pinokio"])
    if os.name == "nt":
        for drive in ("C", "D", "E", "F"):
            homes.append(Path(f"{drive}:/pinokio"))
    else:
        homes.append(user_home / "Library" / "Application Support" / "Pinokio")
    unique: List[Path] = []
    seen: set = set()
    for home in homes:
        key = str(home).lower() if os.name == "nt" else str(home)
        if key not in seen:
            seen.add(key)
            unique.append(home)
    return unique

def _iter_wan2gp_candidate_dirs() -> List[Tuple[Path, Path]]:
    """Yield (app_dir, root) candidates for an existing Wan2GP installation."""
    candidates: List[Tuple[Path, Path]] = []
    # Pinokio installs: <pinokio home>/api/<something with "wan">/app
    for home in _candidate_pinokio_homes():
        api_dir = home / "api"
        try:
            entries = sorted(api_dir.iterdir()) if api_dir.is_dir() else []
        except Exception:
            entries = []
        for entry in entries:
            if entry.is_dir() and "wan" in entry.name.lower():
                candidates.append((entry / "app", entry))
    # AutoYou-managed install plus plain git clones in the user's home folder.
    user_home = Path.home()
    for standalone in (
        MANAGED_WAN2GP_APP_DIR,
        user_home / "Wan2GP",
        user_home / "wan2gp",
        user_home / "WanGP",
    ):
        if standalone.is_dir():
            candidates.append((standalone, standalone.parent if standalone == MANAGED_WAN2GP_APP_DIR else standalone))
    return candidates

def _find_wan2gp_python(app_dir: Path) -> Optional[Path]:
    for rel_path in _wan2gp_python_relpaths():
        python_path = app_dir / rel_path
        if python_path.exists():
            return python_path
    return None

def discover_wan2gp_installation() -> Optional[Dict[str, str]]:
    """Search common per-OS locations for an existing Wan2GP install.

    Returns {root, app_dir, python} for the first candidate that has wgp.py and
    a virtualenv python; falls back to a wgp.py-only match (python="") so the
    UI can report an incomplete environment.
    """
    incomplete: Optional[Dict[str, str]] = None
    for app_dir, root in _iter_wan2gp_candidate_dirs():
        try:
            if not (app_dir / "wgp.py").exists():
                continue
        except Exception:
            continue
        python_path = _find_wan2gp_python(app_dir)
        if python_path is not None:
            return {"root": str(root), "app_dir": str(app_dir), "python": str(python_path)}
        if incomplete is None:
            incomplete = {"root": str(root), "app_dir": str(app_dir), "python": ""}
    return incomplete

def _platform_default_wan2gp_paths() -> Dict[str, str]:
    """OS-appropriate default install paths (standard Pinokio layout)."""
    if os.name == "nt":
        root = "D:/pinokio/api/wan.git"
        return {
            "root": root,
            "app_dir": f"{root}/app",
            "python": f"{root}/app/env/Scripts/python.exe",
        }
    root = str(Path.home() / "pinokio" / "api" / "wan.git")
    return {
        "root": root,
        "app_dir": f"{root}/app",
        "python": f"{root}/app/env/bin/python",
    }

def _default_wan2gp_config() -> Dict[str, Any]:
    """Default settings for the standard Pinokio installation on this OS."""
    return {
        **_platform_default_wan2gp_paths(),
        "model_type": "ltx2_distilled_gguf_q4_k_m",
        "resolution": "416x240",
        "num_inference_steps": 8,
        "video_length": 49,
        "image_model_type": "flux_schnell",
        "image_resolution": "1280x720",
        "image_num_inference_steps": 10,
        "seed": -1,
        "settings_version": 2.52,
        "enhance_prompt": True,
        "prompt_enhancer": "",
    }

def _overlay_discovered_wan2gp_paths(config: Dict[str, Any]) -> Dict[str, Any]:
    """If the configured app folder is missing, fall back to a discovered install.

    Only replaces the path fields (root/app_dir/python) and only when discovery
    finds a complete environment, so saved paths keep priority whenever they
    actually exist on this machine.
    """
    try:
        app_dir = str(config.get("app_dir") or "").strip()
        if app_dir and Path(app_dir).exists():
            return config
    except Exception:
        return config
    discovered = discover_wan2gp_installation()
    if discovered and discovered.get("python"):
        merged = dict(config)
        merged.update(discovered)
        return merged
    return config


def _wan2gp_config_path(*, for_write: bool = False) -> Path:
    """Return the writable runtime path, with a source-tree read fallback."""
    runtime_path = get_config_dir("AutoYou", anchor=__file__) / "config" / "video.json"
    legacy_path = PROJECT_ROOT / "config" / "video.json"
    if not for_write and not is_compiled() and legacy_path.exists() and not runtime_path.exists():
        return legacy_path
    return runtime_path

def get_wan2gp_config() -> Dict[str, Any]:
    """Load Wan2GP configuration from config/video.json, fallback if missing."""
    config_path = _wan2gp_config_path()
    defaults = _default_wan2gp_config()

    # Try importing autoyou_outreach.video.load_video_config safely
    try:
        from autoyou_outreach.video import load_video_config
        outreach_cfg = load_video_config()
        if outreach_cfg and isinstance(outreach_cfg.get("wan2gp"), dict):
            if config_path.exists():
                read_secure_file(config_path)
            return _overlay_discovered_wan2gp_paths({**defaults, **outreach_cfg["wan2gp"]})
    except Exception:
        pass

    if config_path.exists():
        try:
            data = json.loads(read_secure_file(config_path).decode("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("wan2gp"), dict):
                return _overlay_discovered_wan2gp_paths({**defaults, **data["wan2gp"]})
        except SecureStorageError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass

    return _overlay_discovered_wan2gp_paths(defaults)

def save_wan2gp_config(wan_cfg: Dict[str, Any]) -> None:
    """Save Wan2GP configuration to config/video.json."""
    config_path = _wan2gp_config_path(for_write=True)
    existing_config_path = _wan2gp_config_path()
    
    # Read existing config first to merge fields
    current_data = {}
    if existing_config_path.exists():
        try:
            current_data = json.loads(read_secure_file(existing_config_path).decode("utf-8"))
        except SecureStorageError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
            
    if not isinstance(current_data, dict):
        current_data = {}
        
    current_data["wan2gp"] = wan_cfg

    config_path.parent.mkdir(parents=True, exist_ok=True)
    write_secure_file(config_path, json.dumps(current_data, indent=2).encode("utf-8"))

# ── Wan2GP environment detection + machine-dependent install ──────────────────

_INSTALL_LOCK = threading.Lock()
_INSTALL_STATE: Dict[str, Any] = {
    "status": "idle",  # idle | running | completed | failed
    "step": "",
    "error": "",
    "started_at": "",
    "finished_at": "",
    "log_path": str(MEDIA_DATA_DIR / "wan2gp_install.log"),
}

def _machine_profile() -> Dict[str, Any]:
    import platform as _platform

    system = _platform.system()
    machine = _platform.machine().lower()
    has_nvidia = bool(shutil.which("nvidia-smi"))
    return {"system": system, "machine": machine, "has_nvidia": has_nvidia}

def _install_support_for_machine(profile: Dict[str, Any]) -> Tuple[bool, str]:
    """Whether AutoYou's managed Wan2GP install can work here, plus guidance."""
    system = profile.get("system")
    machine = str(profile.get("machine") or "")
    if system == "Darwin":
        if machine in {"arm64", "aarch64"}:
            return True, (
                "Apple Silicon detected. Wan2GP has early MPS support on macOS - "
                "generation is slower than on NVIDIA GPUs and not every model works yet."
            )
        return False, (
            "Intel Macs are not supported: current PyTorch releases no longer ship "
            "Intel-macOS wheels. Run Wan2GP on an Apple Silicon Mac or a Windows/Linux "
            "machine with an NVIDIA GPU, then point these settings at that install."
        )
    if system in {"Windows", "Linux"}:
        if profile.get("has_nvidia"):
            return True, "NVIDIA GPU detected - Wan2GP's primary supported configuration."
        return False, (
            "No NVIDIA GPU detected (nvidia-smi not found). Wan2GP needs an NVIDIA GPU "
            "on this OS; for AMD GPUs use the community 'wan2gp-amd' script inside the "
            "Pinokio app instead."
        )
    return False, f"Unrecognized platform '{system}'."

def wan2gp_environment_status() -> Dict[str, Any]:
    """Full environment report for the Media Generator settings page."""
    profile = _machine_profile()
    install_supported, install_guidance = _install_support_for_machine(profile)
    config = get_wan2gp_config()
    app_dir = Path(str(config.get("app_dir") or ""))
    python_path = Path(str(config.get("python") or ""))
    discovered = discover_wan2gp_installation()
    with _INSTALL_LOCK:
        install_state = dict(_INSTALL_STATE)
    install_state["log_tail"] = _install_log_tail()
    return {
        "platform": profile,
        "configured": {
            "root": str(config.get("root") or ""),
            "app_dir": str(app_dir),
            "python": str(python_path),
            "app_dir_exists": app_dir.exists() if str(app_dir) else False,
            "python_exists": python_path.exists() if str(python_path) else False,
        },
        "discovered": discovered,
        "managed_install_dir": str(MANAGED_WAN2GP_ROOT),
        "install_supported": install_supported,
        "install_guidance": install_guidance,
        "install": install_state,
        "pinokio_hint": (
            "Recommended: install Pinokio (pinokio.computer), then install the 'wan2gp' "
            "community script inside Pinokio. Use 'Detect installation' here afterwards."
        ),
    }

def detect_and_apply_wan2gp_paths() -> Dict[str, Any]:
    """Discover an existing install and persist its paths into the settings."""
    discovered = discover_wan2gp_installation()
    if not discovered:
        return {
            "status": "not_found",
            "message": (
                "No Wan2GP installation was found in the usual folders (Pinokio 'api' "
                "apps, ~/Wan2GP, or AutoYou's managed folder). Install Wan2GP first or "
                "enter the paths manually."
            ),
        }
    if not discovered.get("python"):
        return {
            "status": "incomplete",
            "found": discovered,
            "message": (
                f"Found Wan2GP files at '{discovered['app_dir']}' but no Python "
                "environment inside it. Finish the install (Pinokio: open the app once "
                "so it creates its env), then detect again."
            ),
        }
    config = get_wan2gp_config()
    config.update(discovered)
    save_wan2gp_config(config)
    return {
        "status": "applied",
        "found": discovered,
        "message": f"Wan2GP detected at '{discovered['app_dir']}' and saved to settings.",
    }

def _install_log_tail(max_chars: int = 4000) -> str:
    log_path = Path(_INSTALL_STATE["log_path"])
    try:
        if log_path.exists():
            return read_secure_file(log_path).decode("utf-8", errors="replace")[-max_chars:]
    except SecureStorageError:
        raise
    except OSError:
        pass
    return ""

def _install_log(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(f"[wan2gp-install] {message}")
    try:
        append_secure_file(Path(_INSTALL_STATE["log_path"]), (line + "\n").encode("utf-8"))
    except SecureStorageError:
        raise
    except OSError:
        pass

def _set_install_state(**updates: Any) -> None:
    with _INSTALL_LOCK:
        _INSTALL_STATE.update(updates)

def _find_system_python_for_install() -> Optional[str]:
    """Locate a system Python 3.10-3.12 for the Wan2GP virtualenv."""
    candidates = ["python3.11", "python3.10", "python3.12", "python3", "python"]
    for name in candidates:
        executable = shutil.which(name)
        if not executable:
            continue
        try:
            probe = subprocess.run(
                [executable, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                env=_external_python_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            version = str(probe.stdout or "").strip()
            major, _, minor = version.partition(".")
            if major == "3" and minor.isdigit() and 10 <= int(minor) <= 12:
                return executable
        except Exception:
            continue
    return None

def _run_install_step(step: str, command: List[str], cwd: Optional[Path] = None) -> None:
    _set_install_state(step=step)
    _install_log(f"{step}: {' '.join(command)}")
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=_external_python_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.stdout:
        _install_log(completed.stdout[-2000:])
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-2000:]
        raise RuntimeError(f"{step} failed (exit {completed.returncode}):\n{tail}")

def _run_wan2gp_install_job() -> None:
    profile = _machine_profile()
    try:
        git_executable = shutil.which("git")
        if not git_executable:
            raise RuntimeError(
                "git is not installed or not on PATH. Install git (macOS: xcode-select "
                "--install) and retry."
            )
        system_python = _find_system_python_for_install()
        if not system_python:
            raise RuntimeError(
                "No system Python 3.10-3.12 found on PATH. Install Python 3.11 "
                "(python.org or 'brew install python@3.11') and retry."
            )

        app_dir = MANAGED_WAN2GP_APP_DIR
        MANAGED_WAN2GP_ROOT.mkdir(parents=True, exist_ok=True)
        if (app_dir / ".git").exists():
            _run_install_step("Updating Wan2GP source", [git_executable, "pull", "--ff-only"], cwd=app_dir)
        else:
            _run_install_step(
                "Downloading Wan2GP source",
                [git_executable, "clone", "--depth", "1", WAN2GP_GIT_URL, str(app_dir)],
            )

        env_dir = app_dir / "env"
        venv_python = app_dir / ("env/Scripts/python.exe" if os.name == "nt" else "env/bin/python")
        if not venv_python.exists():
            _run_install_step(
                "Creating Python environment",
                [system_python, "-m", "venv", str(env_dir)],
            )
        _run_install_step(
            "Upgrading pip",
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "wheel"],
        )

        torch_command = [str(venv_python), "-m", "pip", "install", "torch", "torchvision", "torchaudio"]
        if profile.get("system") == "Windows" and profile.get("has_nvidia"):
            torch_command += ["--index-url", "https://download.pytorch.org/whl/cu124"]
        _run_install_step("Installing PyTorch (large download)", torch_command)

        requirements = app_dir / "requirements.txt"
        if requirements.exists():
            _run_install_step(
                "Installing Wan2GP requirements (large download)",
                [str(venv_python), "-m", "pip", "install", "-r", str(requirements)],
            )

        if not (app_dir / "wgp.py").exists():
            raise RuntimeError(f"Install finished but wgp.py is missing in {app_dir}.")
        if not venv_python.exists():
            raise RuntimeError(f"Install finished but the environment python is missing at {venv_python}.")

        config = get_wan2gp_config()
        config.update(
            {
                "root": str(MANAGED_WAN2GP_ROOT),
                "app_dir": str(app_dir),
                "python": str(venv_python),
            }
        )
        save_wan2gp_config(config)
        _install_log("Install completed. Settings now point at the managed install.")
        _set_install_state(
            status="completed",
            step="done",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        _install_log(f"ERROR: {exc}")
        _set_install_state(
            status="failed",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

def start_wan2gp_install() -> Dict[str, Any]:
    """Kick off a machine-appropriate managed Wan2GP install in the background."""
    profile = _machine_profile()
    supported, guidance = _install_support_for_machine(profile)
    if not supported:
        return {"status": "unsupported", "message": guidance}
    with _INSTALL_LOCK:
        if _INSTALL_STATE["status"] == "running":
            return {"status": "already_running", "message": "An install is already in progress."}
        _INSTALL_STATE.update(
            {
                "status": "running",
                "step": "starting",
                "error": "",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": "",
            }
        )
    try:
        write_secure_file(Path(_INSTALL_STATE["log_path"]), b"")
    except SecureStorageError:
        raise
    except OSError:
        pass
    _install_log(f"Starting managed Wan2GP install for {profile['system']}/{profile['machine']}. {guidance}")
    threading.Thread(target=_run_wan2gp_install_job, daemon=True, name="Wan2GP-Install-Thread").start()
    return {
        "status": "started",
        "message": (
            "Install started in the background. This downloads several gigabytes "
            "(PyTorch + dependencies) and can take a long time; watch the progress log."
        ),
    }

def get_wan2gp_install_status() -> Dict[str, Any]:
    with _INSTALL_LOCK:
        state = dict(_INSTALL_STATE)
    state["log_tail"] = _install_log_tail()
    return state

def save_history_item(
    media_type: str,
    original_prompt: str,
    optimized_prompt: str,
    file_path: str,
    file_name: str,
    settings: Dict[str, Any],
    status: str,
    error_message: Optional[str] = None
) -> int:
    """Save generation item to the history database."""
    conn = sqlite3.connect(DB_PATH)
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """
            INSERT INTO media_history (
                timestamp, media_type, original_prompt, optimized_prompt,
                file_path, file_name, model_type, resolution, steps, frames, seed, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                media_type,
                original_prompt,
                optimized_prompt,
                file_path,
                file_name,
                settings.get("model_type"),
                settings.get("resolution"),
                settings.get("num_inference_steps"),
                settings.get("video_length"),
                settings.get("seed"),
                status,
                error_message
            )
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def update_history_status(
    item_id: int,
    status: str,
    error_message: Optional[str] = None,
    *,
    optimized_prompt: Optional[str] = None,
    file_path: Optional[str] = None,
    file_name: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> None:
    """Update status of a history item (used for background jobs)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        assignments = ["status = ?", "error_message = ?"]
        values: List[Any] = [status, error_message]
        if optimized_prompt is not None:
            assignments.append("optimized_prompt = ?")
            values.append(optimized_prompt)
        if file_path is not None:
            assignments.append("file_path = ?")
            values.append(file_path)
        if file_name is not None:
            assignments.append("file_name = ?")
            values.append(file_name)
        if settings:
            column_map = {
                "model_type": "model_type",
                "resolution": "resolution",
                "num_inference_steps": "steps",
                "video_length": "frames",
                "seed": "seed",
            }
            for source_key, column_name in column_map.items():
                if source_key in settings:
                    assignments.append(f"{column_name} = ?")
                    values.append(settings.get(source_key))
        values.append(item_id)
        conn.execute(
            f"UPDATE media_history SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()

def list_history(limit: int = 50, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve media generation runs from SQLite history db."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM media_history"
        params = []
        if media_type:
            query += " WHERE media_type = ?"
            params.append(media_type)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_history_item(item_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve details of a specific past generation."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM media_history WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def delete_history_item(item_id: int) -> bool:
    """Delete entry and associated file from system."""
    item = get_history_item(item_id)
    if not item:
        return False
        
    # Attempt to delete file
    try:
        f_path = Path(item["file_path"])
        if f_path.exists():
            f_path.unlink()
    except SecureStorageError:
        raise
    except OSError:
        pass
        
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM media_history WHERE id = ?", (item_id,))
        conn.commit()
        return True
    finally:
        conn.close()

def _normalize_media_type(media_type: str) -> str:
    normalized = str(media_type or "video").strip().lower()
    if normalized not in VALID_MEDIA_TYPES:
        raise ValueError("media_type must be either 'video' or 'image'")
    return normalized

def _normalize_model_type(model_type: Optional[str]) -> Optional[str]:
    value = str(model_type or "").strip()
    if not value:
        return None
    return MODEL_TYPE_ALIASES.get(value.lower(), value)

def _clean_prompt_text(prompt: str, *, max_chars: int = PROMPT_MAX_CHARS) -> str:
    text = str(prompt or "").strip()
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"[*_`#>\[\]{}]+", "", text)
    text = re.sub(r"\s*[-•]\s+", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    if len(text) > max_chars:
        cutoff = text.rfind(".", 0, max_chars)
        if cutoff < max_chars // 2:
            cutoff = text.rfind(",", 0, max_chars)
        if cutoff < max_chars // 2:
            cutoff = max_chars
        text = text[:cutoff].rstrip(" ,.;") + "..."
    return text

def _heuristic_enhance_prompt(prompt: str, media_type: str = "video") -> str:
    prompt = _clean_prompt_text(prompt, max_chars=600)
    if media_type == "video":
        return _clean_prompt_text(
            f"{prompt}. Cinematic realistic shot, detailed subject and environment, natural lighting, "
            "smooth camera movement, centered composition. No readable text, no logos, and no watermark "
            "appear anywhere in the frame. The composition keeps the action centered for vertical crop compatibility."
        )
    return _clean_prompt_text(
        f"High-detail cinematic image of {prompt}, clean composition, expressive subject, rich lighting, "
        "balanced color, sharp focus, premium texture detail. No readable text, no logos, and no watermark."
    )

def _load_wan_model_definition(app_dir: Path, model_type: str) -> Dict[str, Any]:
    defaults_dir = app_dir / "defaults"
    candidate = defaults_dir / f"{model_type}.json"
    if not candidate.is_file():
        return {}
    try:
        with candidate.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}

def _model_supports_media_type(app_dir: Path, model_type: str, media_type: str) -> bool:
    model_type = _normalize_model_type(model_type) or ""
    if not model_type:
        return False
    definition = _load_wan_model_definition(app_dir, model_type)
    model = definition.get("model") if isinstance(definition.get("model"), dict) else {}
    architecture = str(model.get("architecture") or model_type).lower()
    image_capable = bool(model.get("image_outputs")) or architecture.startswith(
        ("flux", "qwen_image", "z_image")
    ) or model_type.lower().startswith(("flux", "qwen_image", "z_image"))
    if media_type == "image":
        return image_capable
    return not image_capable

def _resolve_wan_model_type(
    wan: Dict[str, Any],
    app_dir: Path,
    requested_model_type: Optional[str],
    media_type: str,
) -> Tuple[Optional[str], Optional[str]]:
    requested = _normalize_model_type(requested_model_type)
    if requested and _model_supports_media_type(app_dir, requested, media_type):
        return requested, None

    if media_type == "image":
        candidates = [
            _normalize_model_type(wan.get("image_model_type")),
            *DEFAULT_IMAGE_MODEL_CANDIDATES,
        ]
    else:
        candidates = [
            _normalize_model_type(wan.get("model_type")),
            "ltx2_distilled_gguf_q4_k_m",
            "ltx2_distilled",
            "t2v",
        ]

    for candidate in candidates:
        if candidate and _model_supports_media_type(app_dir, candidate, media_type):
            warning = None
            if requested and requested != candidate:
                warning = (
                    f"Requested model '{requested}' is not configured as a Wan2GP {media_type} model; "
                    f"using '{candidate}' instead."
                )
            return candidate, warning

    if requested:
        return None, f"Requested model '{requested}' is not a valid Wan2GP {media_type} model."
    return None, f"No Wan2GP {media_type} model is configured."

def _tail_text(text: str, limit: int = 2000) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[-limit:]

def _write_process_logs(work_dir: Path, command: List[str], completed: subprocess.CompletedProcess[str]) -> None:
    try:
        write_secure_file(
            work_dir / "command.json",
            json.dumps(
                {
                    "command": command,
                    "returncode": completed.returncode,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ).encode("utf-8"),
        )
        write_secure_file(work_dir / "stdout.txt", str(completed.stdout or "").encode("utf-8", errors="replace"))
        write_secure_file(work_dir / "stderr.txt", str(completed.stderr or "").encode("utf-8", errors="replace"))
    except SecureStorageError:
        raise
    except OSError:
        pass


def _seal_generation_work_dir(work_dir: Path) -> None:
    """Migrate all completed AutoYou-owned work files to the secure boundary."""

    for candidate in work_dir.rglob("*"):
        if candidate.is_file():
            read_secure_file(candidate)

WAN_MEMORY_FAILURE_MARKERS = (
    "bad allocation",
    "memory allocation failure",
    "cuda out of memory",
    "outofmemoryerror",
)

def _looks_like_wan_memory_failure(*texts: Any) -> bool:
    combined = "\n".join(str(text or "") for text in texts).lower()
    return any(marker in combined for marker in WAN_MEMORY_FAILURE_MARKERS)

def _parse_resolution(resolution: Any) -> Optional[Tuple[int, int]]:
    match = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", str(resolution or ""), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        width = int(match.group(1))
        height = int(match.group(2))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height

def _scale_dimension_to_multiple(value: float, *, minimum: int = 256, multiple: int = 16) -> int:
    scaled = int(round(float(value) / multiple) * multiple)
    return max(minimum, scaled)

def _low_memory_image_resolution(resolution: Any, *, max_long_edge: int = 768) -> Optional[str]:
    parsed = _parse_resolution(resolution)
    if not parsed:
        fallback = "768x432"
        return None if str(resolution or "").strip().lower() == fallback else fallback

    width, height = parsed
    long_edge = max(width, height)
    if long_edge <= max_long_edge:
        return None

    scale = max_long_edge / long_edge
    fallback_width = _scale_dimension_to_multiple(width * scale)
    fallback_height = _scale_dimension_to_multiple(height * scale)
    fallback = f"{fallback_width}x{fallback_height}"
    return None if fallback == f"{width}x{height}" else fallback

def _low_memory_image_prompt(original_prompt: Any, generated_prompt: Any) -> str:
    compact_original = _clean_prompt_text(original_prompt, max_chars=360)
    compact_generated = _clean_prompt_text(generated_prompt, max_chars=360)
    if compact_original and len(compact_original) < max(96, int(len(compact_generated) * 0.65)):
        return compact_original
    return compact_generated or compact_original

def _unique_work_dir(base_dir: Path) -> Path:
    candidate = base_dir
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = base_dir.with_name(f"{base_dir.name}_{suffix}")
    return candidate

def ai_enhance_prompt(prompt: str, media_type: str = "video") -> str:
    """Optimize the prompt using active AutoYou model provider (Gemini, Ollama, LiteLLM, OpenClaw)."""
    try:
        import litellm
        import logging
        logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
        if hasattr(litellm, "logger"):
            litellm.logger.setLevel(50) # Critical/No logs
    except Exception:
        return _heuristic_enhance_prompt(prompt, media_type)
    
    provider = os.getenv("AI_PROVIDER", "ollama").strip().lower()
    
    if media_type == "video":
        system_instruction = (
            "You are a cinematic prompt engineer expert for Wan2.1 and LTX-Video text-to-video models.\n"
            "Optimize the user's prompt into one concise descriptive paragraph under 140 words with no line breaks or Markdown.\n"
            "Describe the scene chronologically: start with visible action, specify subjects, environment, lighting, and camera movement.\n"
            "Keep the composition center-safe for vertical cropping.\n"
            "Always append: 'No readable text, no logos, and no watermark appear anywhere in the frame. The composition keeps the action centered for vertical crop compatibility.'\n"
            "Output ONLY the final prompt and nothing else. Do not add quotes or preamble."
        )
    else:
        system_instruction = (
            "You are a professional visual prompt engineer expert for text-to-image models (like Flux or Qwen Image).\n"
            "Optimize the user's prompt into one concise descriptive paragraph under 120 words with no line breaks or Markdown.\n"
            "Specify subject, composition, lighting, style, depth of field, color, and texture details.\n"
            "Output ONLY the final prompt and nothing else. Do not add quotes or preamble."
        )

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"Optimize this prompt: {prompt}"}
    ]

    completion_kwargs = {
        "messages": messages,
        "max_tokens": 360,
        "temperature": 0.4,
        "timeout": 45,
    }

    try:
        if provider == "google":
            model = "gemini/" + os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
            response = litellm.completion(model=model, **completion_kwargs)
        elif provider == "openclaw":
            port = os.getenv("OPENCLAW_PORT", "18789")
            model = "openai/" + os.getenv("OPENCLAW_MODEL", "openclaw/default")
            response = litellm.completion(
                model=model,
                api_base=f"http://127.0.0.1:{port}/v1",
                api_key=os.getenv("OPENCLAW_TOKEN", "noop"),
                **completion_kwargs,
            )
        elif provider == "hermes":
            port = os.getenv("HERMES_PORT", "8642")
            model = "openai/" + os.getenv("HERMES_MODEL", "hermes-agent")
            response = litellm.completion(
                model=model,
                api_base=f"http://127.0.0.1:{port}/v1",
                api_key=os.getenv("HERMES_TOKEN", "noop"),
                **completion_kwargs,
            )
        elif provider == "litellm":
            model = os.getenv("LITELLM_MODEL", "")
            response = litellm.completion(
                model=model,
                api_key=os.getenv("LITELLM_API_KEY"),
                api_base=os.getenv("LITELLM_API_BASE"),
                **completion_kwargs,
            )
        else: # Default local Ollama
            model_name = os.getenv("OLLAMA_MODEL", "ministral-3:8b")
            api_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
            response = litellm.completion(
                model=f"ollama_chat/{model_name}",
                api_base=api_base,
                **completion_kwargs,
            )
            
        enhanced = _clean_prompt_text(response.choices[0].message.content)
        if (enhanced.startswith('"') and enhanced.endswith('"')) or (enhanced.startswith("'") and enhanced.endswith("'")):
            enhanced = enhanced[1:-1].strip()
        return enhanced or _heuristic_enhance_prompt(prompt, media_type)
    except Exception as e:
        print(f"Warning: AI prompt enhancement failed: {e}. Using heuristic template.")
        return _heuristic_enhance_prompt(prompt, media_type)

def generate_media_sync(
    prompt: str,
    media_type: str = "video",
    model_type: Optional[str] = None,
    resolution: Optional[str] = None,
    steps: Optional[int] = None,
    frames: Optional[int] = None,
    seed: Optional[int] = None,
    history_item_id: Optional[int] = None,
    optimized_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute local Wan2GP generation synchronously, patiently waiting for result."""
    wan = get_wan2gp_config()
    media_type = _normalize_media_type(media_type)
    
    # 1. Resolve paths (platform defaults; get_wan2gp_config already overlays a
    #    discovered install when the saved path is missing on this machine)
    default_paths = _platform_default_wan2gp_paths()
    app_dir = Path(wan.get("app_dir", default_paths["app_dir"]))
    python = Path(wan.get("python", default_paths["python"]))

    # 2. Check if python and app directory are present on this machine
    if not app_dir.exists():
        err_msg = (
            f"Wan2GP application folder not found at: '{app_dir}'. "
            "Wan2GP (by deepbeepmeep) is not installed here yet, or lives in another folder. "
            "Open the Media Generator Settings page and use 'Detect installation' - or "
            "'Install Wan2GP' to set up a machine-appropriate copy automatically."
        )
        if history_item_id:
            update_history_status(history_item_id, "failed", err_msg)
        return {"status": "error", "message": err_msg, "install_required": True}

    if not python.exists():
        err_msg = (
            f"Wan2GP Python executable not found at: '{python}'. "
            "The Wan2GP environment has not finished setting up (Pinokio creates it on the "
            "app's first launch). Open the Media Generator Settings page and use 'Detect "
            "installation' after setup completes, or correct the Python path manually."
        )
        if history_item_id:
            update_history_status(history_item_id, "failed", err_msg)
        return {"status": "error", "message": err_msg, "install_required": True}

    final_model_type, model_warning = _resolve_wan_model_type(wan, app_dir, model_type, media_type)
    if not final_model_type:
        err_msg = (
            f"{model_warning} Configure a {media_type} model in the Media Generator settings. "
            "For image generation, use an image-capable Wan2GP model such as flux_schnell, flux, z_image, or qwen_image_20B."
        )
        if history_item_id:
            update_history_status(history_item_id, "failed", err_msg)
        return {"status": "error", "message": err_msg}

    # 3. Enhance prompt using AI only when enabled, and keep the payload Wan2GP-friendly.
    if optimized_prompt:
        final_prompt = _clean_prompt_text(optimized_prompt)
    elif bool(wan.get("enhance_prompt", True)):
        final_prompt = ai_enhance_prompt(prompt, media_type)
    else:
        final_prompt = _clean_prompt_text(prompt)
    if history_item_id:
        update_history_status(history_item_id, "generating", None, optimized_prompt=final_prompt)

    # 4. Prepare parameters
    final_resolution = resolution or (
        wan.get("image_resolution") if media_type == "image" else wan.get("resolution")
    ) or ("1280x720" if media_type == "image" else "416x240")
    final_steps = int(
        steps
        or (wan.get("image_num_inference_steps") if media_type == "image" else wan.get("num_inference_steps"))
        or (10 if media_type == "image" else 8)
    )
    final_frames = None if media_type == "image" else int(frames or wan.get("video_length", 49))
    final_seed = int(seed if seed is not None else wan.get("seed", -1))
    
    base_settings_payload = {
        "settings_version": float(wan.get("settings_version", 2.52)),
        "model_type": final_model_type,
        "seed": final_seed,
        "image_mode": 1 if media_type == "image" else 0,
        "batch_size": 1,
        "audio_prompt_type": "",
        "video_prompt_type": "",
        "multi_prompts_gen_type": 0,
        "enhance_prompt": False,
        "prompt_enhancer": str(wan.get("prompt_enhancer", "")),
        "repeat_generation": 1,
        "flow_shift": 5.0,
        "sample_solver": "unipc",
        "force_fps": "24",
        "output_filename": "autoyou_{date(YYYY-MM-DD_HH-mm-ss)}_{prompt(32)}",
    }
    if media_type == "video":
        base_settings_payload["video_length"] = final_frames

    settings_recorded = {
        "model_type": final_model_type,
        "resolution": final_resolution,
        "num_inference_steps": final_steps,
        "video_length": final_frames,
        "seed": final_seed
    }
    if history_item_id:
        update_history_status(history_item_id, "generating", None, settings=settings_recorded)

    work_dir: Optional[Path] = None
    try:
        attempts: List[Dict[str, Any]] = [
            {
                "label": "primary",
                "prompt": final_prompt,
                "resolution": final_resolution,
                "num_inference_steps": final_steps,
            }
        ]
        if media_type == "image":
            retry_resolution = _low_memory_image_resolution(final_resolution) or final_resolution
            retry_prompt = _low_memory_image_prompt(prompt, final_prompt)
            retry_steps = min(final_steps, 6)
            if (
                retry_resolution != final_resolution
                or retry_prompt != final_prompt
                or retry_steps != final_steps
            ):
                attempts.append(
                    {
                        "label": "low-memory-retry",
                        "prompt": retry_prompt,
                        "resolution": retry_resolution,
                        "num_inference_steps": retry_steps,
                    }
                )

        job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        retry_notes: List[str] = []
        allowed_extensions = IMAGE_OUTPUT_EXTENSIONS if media_type == "image" else VIDEO_OUTPUT_EXTENSIONS

        for attempt_index, attempt in enumerate(attempts):
            attempt_prompt = str(attempt["prompt"])
            attempt_resolution = str(attempt["resolution"])
            attempt_steps = int(attempt["num_inference_steps"])
            attempt_settings_recorded = {
                "model_type": final_model_type,
                "resolution": attempt_resolution,
                "num_inference_steps": attempt_steps,
                "video_length": final_frames,
                "seed": final_seed,
            }

            # Create temporary folders & config files
            work_dir_name = job_id if attempt_index == 0 else f"{job_id}_retry{attempt_index}"
            work_dir = _unique_work_dir(OUTPUT_DIR / work_dir_name)
            work_dir.mkdir(parents=True, exist_ok=True)

            settings_payload = dict(base_settings_payload)
            settings_payload.update(
                {
                    "prompt": attempt_prompt,
                    "resolution": attempt_resolution,
                    "num_inference_steps": attempt_steps,
                }
            )

            settings_path = work_dir / "wan2gp-settings.json"
            with settings_path.open("w", encoding="utf-8") as f:
                json.dump(settings_payload, f, indent=2)

            output_dir = work_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            # 5. Build executable command
            command = [
                str(python),
                "wgp.py",
                "--process",
                str(settings_path),
                "--output-dir",
                str(output_dir),
            ]

            completed = subprocess.run(
                command,
                cwd=app_dir,
                env=_external_python_subprocess_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            _write_process_logs(work_dir, command, completed)

            if completed.returncode != 0:
                if (
                    attempt_index + 1 < len(attempts)
                    and _looks_like_wan_memory_failure(completed.stderr, completed.stdout)
                ):
                    next_attempt = attempts[attempt_index + 1]
                    note = (
                        f"Wan2GP reported memory pressure; logs were saved in {work_dir}. "
                        f"Retrying image generation at {next_attempt['resolution']} with a shorter prompt."
                    )
                    retry_notes.append(note)
                    if history_item_id:
                        update_history_status(
                            history_item_id,
                            "generating",
                            note,
                            optimized_prompt=str(next_attempt["prompt"]),
                            settings={
                                "model_type": final_model_type,
                                "resolution": str(next_attempt["resolution"]),
                                "num_inference_steps": int(next_attempt["num_inference_steps"]),
                                "video_length": final_frames,
                                "seed": final_seed,
                            },
                        )
                    _seal_generation_work_dir(work_dir)
                    continue

                err_msg = (
                    f"Wan2GP generation failed with exit code {completed.returncode}. "
                    f"Logs were saved in {work_dir}.\nSTDERR tail:\n{_tail_text(completed.stderr)}\n"
                    f"STDOUT tail:\n{_tail_text(completed.stdout)}"
                )
                if retry_notes:
                    err_msg = "\n".join(retry_notes) + "\n" + err_msg
                if history_item_id:
                    update_history_status(history_item_id, "failed", err_msg)
                _seal_generation_work_dir(work_dir)
                return {"status": "error", "message": err_msg}

            files = [
                candidate
                for candidate in output_dir.glob("*")
                if candidate.is_file() and candidate.suffix.lower() in allowed_extensions
            ]

            if not files:
                err_msg = (
                    f"Wan2GP exited successfully but no {media_type} output file was detected in {output_dir}. "
                    "This usually means Wan2GP skipped the task during validation, the selected model is not installed, "
                    "or the settings did not match the requested media type. "
                    f"Logs were saved in {work_dir}.\nSTDOUT tail:\n{_tail_text(completed.stdout)}\n"
                    f"STDERR tail:\n{_tail_text(completed.stderr)}"
                )
                if retry_notes:
                    err_msg = "\n".join(retry_notes) + "\n" + err_msg
                if history_item_id:
                    update_history_status(history_item_id, "failed", err_msg)
                _seal_generation_work_dir(work_dir)
                return {"status": "error", "message": err_msg}

            # Select the latest or best file
            result_file = sorted(files, key=lambda p: p.stat().st_mtime)[-1]

            # Copy to local workspace storage
            permanent_dir = OUTPUT_DIR / ("images" if media_type == "image" else "videos")
            permanent_dir.mkdir(parents=True, exist_ok=True)

            final_file_path = permanent_dir / result_file.name
            write_secure_file(final_file_path, result_file.read_bytes())
            _seal_generation_work_dir(work_dir)

            # Cleanup successful work dir
            try:
                shutil.rmtree(work_dir)
            except Exception:
                pass

            # Update or create history entry
            if history_item_id:
                update_history_status(
                    history_item_id,
                    "completed",
                    None,
                    optimized_prompt=attempt_prompt,
                    file_path=str(final_file_path),
                    file_name=result_file.name,
                    settings=attempt_settings_recorded,
                )
                item_id = history_item_id
            else:
                item_id = save_history_item(
                    media_type=media_type,
                    original_prompt=prompt,
                    optimized_prompt=attempt_prompt,
                    file_path=str(final_file_path),
                    file_name=result_file.name,
                    settings=attempt_settings_recorded,
                    status="completed"
                )

            message = f"Successfully generated {media_type} and saved to history."
            if retry_notes:
                message += " Wan2GP memory pressure was recovered with a low-memory retry."

            return {
                "status": "success",
                "item_id": item_id,
                "media_type": media_type,
                "file_path": str(final_file_path),
                "file_name": result_file.name,
                "optimized_prompt": attempt_prompt,
                "model_warning": model_warning,
                "retry_attempted": bool(retry_notes),
                "message": message
            }

        err_msg = "No Wan2GP generation attempt was available."
        if history_item_id:
            update_history_status(history_item_id, "failed", err_msg)
        return {"status": "error", "message": err_msg}
        
    except SecureStorageError:
        raise
    except Exception as e:
        if work_dir is not None:
            try:
                _seal_generation_work_dir(work_dir)
            except Exception:
                pass
        err_msg = f"Unexpected error during subprocess execution: {str(e)}"
        if history_item_id:
            update_history_status(history_item_id, "failed", err_msg)
        return {"status": "error", "message": err_msg}

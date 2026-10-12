# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-5fa18c3d2b29ea772791673c

"""
whisper.cpp binary downloader for compiled AutoYou builds.

When RealtimeSTT is unavailable in packaged builds, this module provides a
fallback STT pipeline backed by whisper.cpp binaries.

Usage:
    from shared.whisper_downloader import get_whisper_cpp_binary, download_whisper_model

    binary = get_whisper_cpp_binary()      # downloads once, caches in user data dir
    model  = download_whisper_model("tiny.en")

The WhisperCppRecorder class at the bottom implements the same minimal interface
expected by AudioManager._transcription_loop:
    recorder.feed_audio(bytes)    - feed raw 16 kHz mono s16-LE PCM
    recorder.text()               - blocking: return next transcription (or "")
    recorder.start()
    recorder.stop()
    recorder.shutdown()
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import io
import logging
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-5fa18c3d2b29ea772791673c"


LOGGER = logging.getLogger("autoyou.whisper_downloader")

# ─── Release info ────────────────────────────────────────────────────────────
# whisper.cpp v1.7.2+ provides pre-built Windows x64 binaries on GitHub releases.
_GITHUB_RELEASE_API = (
    "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest"
)
# Fallback hard-coded URL in case the GitHub API is unavailable.
_WIN_FALLBACK_URL = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.4/"
    "whisper-bin-x64.zip"
)
_GGML_MODEL_BASE = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
)

_WINDOWS_BINARY_CANDIDATES = (
    "whisper-whisper-cli.exe",
    "whisper-cli.exe",
    "main.exe",
)
_WINDOWS_REQUIRED_DLLS = (
    "whisper.dll",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-cpu.dll",
)
_WINDOWS_DEPRECATED_BINARY_MARKER = "is deprecated"
_WINDOWS_HELP_USAGE_MARKERS = (
    "usage:",
    "supported audio formats",
)
_POSIX_BINARY_CANDIDATES = (
    "whisper-cli",
    "whisper",
    "main",
)


def _hidden_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}

    kwargs: dict[str, object] = {}
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window

    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo

    return kwargs


# ─── Path helpers ────────────────────────────────────────────────────────────

def _tools_dir() -> Path:
    from shared.platform_runtime import get_user_data_dir
    d = get_user_data_dir("AutoYou") / "tools" / "whisper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _models_dir() -> Path:
    d = _tools_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iter_bundled_model_dirs() -> tuple[Path, ...]:
    candidates: list[Path] = []

    env_candidate = os.getenv("AUTOYOU_WHISPER_MODELS_DIR", "").strip()
    if env_candidate:
        candidates.append(Path(env_candidate).expanduser())

    try:
        from shared.platform_runtime import find_bundled_whisper_models_dir

        bundled_models = find_bundled_whisper_models_dir(__file__)
        if bundled_models is not None:
            candidates.append(bundled_models)
    except Exception:
        pass

    bundled_runtime = _bundled_runtime_dir()
    if bundled_runtime is not None:
        candidates.append(bundled_runtime / "models")

    ordered: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        normalized = str(resolved)
        if normalized in seen:
            continue
        seen.add(normalized)
        if resolved.is_dir():
            ordered.append(resolved)

    return tuple(ordered)


def _find_bundled_model(model_filename: str) -> Optional[Path]:
    for models_dir in _iter_bundled_model_dirs():
        candidate = models_dir / model_filename
        if candidate.is_file():
            return candidate.resolve()
    return None


def _bundled_runtime_dir() -> Optional[Path]:
    if sys.platform not in {"win32", "darwin"}:
        return None

    try:
        from shared.platform_runtime import get_resources_root, is_compiled

        if not is_compiled():
            return None

        runtime_dir = get_resources_root(__file__) / "runtime" / "whisper"
    except Exception:
        return None

    return runtime_dir if runtime_dir.is_dir() else None


def _probe_posix_binary(binary_path: Path) -> bool:
    for help_flag in ("-h", "--help"):
        try:
            result = subprocess.run(
                [str(binary_path), help_flag],
                capture_output=True,
                text=True,
                timeout=10,
                **_hidden_subprocess_kwargs(),
            )
        except Exception as exc:
            LOGGER.debug("Unable to probe whisper.cpp binary %s: %s", binary_path, exc)
            return False

        output = "\n".join(part for part in (result.stdout, result.stderr) if part).lower()
        if all(marker in output for marker in _WINDOWS_HELP_USAGE_MARKERS):
            return True
        if "usage:" in output and "whisper" in output:
            return True

    return False


def _resolve_posix_binary(runtime_root: Path) -> Optional[Path]:
    for search_root in (runtime_root, runtime_root / "bin"):
        for name in _POSIX_BINARY_CANDIDATES:
            candidate = search_root / name
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            if _probe_posix_binary(candidate):
                return candidate

    return None


def _probe_windows_binary(binary_path: Path) -> tuple[bool, bool]:
    try:
        result = subprocess.run(
            [str(binary_path), "-h"],
            capture_output=True,
            text=True,
            timeout=10,
            **_hidden_subprocess_kwargs(),
        )
    except Exception as exc:
        LOGGER.debug("Unable to probe whisper.cpp binary %s: %s", binary_path, exc)
        return False, False

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).lower()
    is_deprecated = _WINDOWS_DEPRECATED_BINARY_MARKER in output
    is_cli = all(marker in output for marker in _WINDOWS_HELP_USAGE_MARKERS)
    return is_cli, is_deprecated


def _resolve_windows_binary(runtime_root: Path) -> Optional[Path]:
    for name in _WINDOWS_BINARY_CANDIDATES:
        candidate = runtime_root / name
        if not candidate.is_file():
            continue

        is_cli, is_deprecated = _probe_windows_binary(candidate)
        if is_deprecated:
            LOGGER.info("Ignoring deprecated whisper.cpp binary: %s", candidate)
            continue

        if is_cli:
            return candidate

    return None


def _has_required_windows_dlls(runtime_root: Path) -> bool:
    has_whisper = (runtime_root / "whisper.dll").is_file()
    has_ggml = (runtime_root / "ggml.dll").is_file()
    has_cpu = (
        (runtime_root / "ggml-cpu.dll").is_file()
        or (runtime_root / "ggml-base.dll").is_file()
        or any(runtime_root.glob("ggml-cpu-*.dll"))
    )
    return has_whisper and has_ggml and has_cpu


def _find_windows_bundle_source(search_root: Path) -> Optional[Path]:
    candidate_roots: list[Path] = []

    def add_candidate(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in candidate_roots:
            candidate_roots.append(resolved)

    add_candidate(search_root)
    for binary_name in _WINDOWS_BINARY_CANDIDATES:
        for binary_path in search_root.rglob(binary_name):
            if binary_path.is_file():
                add_candidate(binary_path.parent)

    for candidate_root in candidate_roots:
        if _resolve_windows_binary(candidate_root) is not None and _has_required_windows_dlls(candidate_root):
            return candidate_root

    return None


def _copy_windows_bundle_files(source_dir: Path, dest_dir: Path) -> Optional[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)

    source_binary = _resolve_windows_binary(source_dir)
    if source_binary is None:
        return None

    for binary_name in _WINDOWS_BINARY_CANDIDATES:
        if binary_name == source_binary.name:
            continue

        try:
            (dest_dir / binary_name).unlink(missing_ok=True)
        except Exception:
            pass

    destination_binary = dest_dir / source_binary.name
    shutil.copy2(source_binary, destination_binary)

    for dll_path in source_dir.glob("*.dll"):
        shutil.copy2(dll_path, dest_dir / dll_path.name)

    return destination_binary


def _clear_windows_bundle_files(dest_dir: Path) -> None:
    for filename in {*list(_WINDOWS_BINARY_CANDIDATES), *list(_WINDOWS_REQUIRED_DLLS)}:
        try:
            (dest_dir / filename).unlink(missing_ok=True)
        except Exception:
            pass


def ensure_whisper_cpp_runtime_bundle(force: bool = False) -> Optional[Path]:
    if sys.platform != "win32":
        return None

    bundled_runtime = _bundled_runtime_dir()
    if bundled_runtime is not None:
        bundled_binary = _resolve_windows_binary(bundled_runtime)
        if bundled_binary is not None and _has_required_windows_dlls(bundled_runtime):
            return bundled_runtime

    tools = _tools_dir()
    if force:
        _clear_windows_bundle_files(tools)

    existing_binary = _resolve_windows_binary(tools)
    if existing_binary is not None and _has_required_windows_dlls(tools):
        return tools

    source_dir = _find_windows_bundle_source(tools)
    if source_dir is not None:
        materialized_binary = _copy_windows_bundle_files(source_dir, tools)
        if materialized_binary is not None and _has_required_windows_dlls(tools):
            return tools

    downloaded_binary = _download_windows_binary(tools)
    if downloaded_binary is not None and _has_required_windows_dlls(tools):
        return tools

    return None


# ─── Binary discovery / download ─────────────────────────────────────────────

def _binary_name() -> str:
    """Return the whisper-cli binary name for the current platform."""
    if sys.platform == "win32":
        # Windows runtime resolution is dynamic and validated at runtime.
        return "whisper-cli.exe"
    return "whisper-cli"


def get_whisper_cpp_binary(force: bool = False) -> Optional[Path]:
    """
    Return the path to a usable whisper-cli binary.

    On Windows, downloads and unpacks the pre-built release zip from GitHub.
    On macOS/Linux, tries to find an installed `whisper-cli` or `whisper` on
    PATH; returns None if nothing is found (user must build manually).
    """
    bundled_runtime = _bundled_runtime_dir()
    if bundled_runtime is not None:
        if sys.platform == "win32":
            bundled_binary = _resolve_windows_binary(bundled_runtime)
        else:
            bundled_binary = _resolve_posix_binary(bundled_runtime)
        if bundled_binary is not None:
            return bundled_binary

    if sys.platform == "win32":
        runtime_dir = ensure_whisper_cpp_runtime_bundle(force=force)
        if runtime_dir is None:
            return None

        binary = _resolve_windows_binary(runtime_dir)
        if binary is not None:
            return binary

        return None
    return _find_system_whisper()


def _download_windows_binary(tools: Path) -> Optional[Path]:
    """Download the whisper.cpp Windows x64 zip and extract it."""
    LOGGER.info("Downloading whisper.cpp Windows binary...")

    zip_url = _WIN_FALLBACK_URL
    # Try to get the latest release URL from GitHub API
    try:
        import json as _json
        req = urllib.request.Request(
            _GITHUB_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "AutoYou"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            release = _json.loads(resp.read())
        preferred_assets = []
        for asset in release.get("assets", []):
            name = str(asset.get("name", "") or "")
            lower_name = name.lower()
            if not lower_name.endswith(".zip") or "x64" not in lower_name or "win32" in lower_name:
                continue

            if lower_name == "whisper-bin-x64.zip":
                priority = 0
            elif lower_name == "whisper-blas-bin-x64.zip":
                priority = 1
            elif "win" in lower_name:
                priority = 2
            else:
                priority = 3

            preferred_assets.append((priority, name, asset.get("browser_download_url", "")))

        if preferred_assets:
            preferred_assets.sort(key=lambda item: (item[0], item[1]))
            _, selected_name, selected_url = preferred_assets[0]
            if selected_url:
                zip_url = selected_url
                LOGGER.info("Found latest whisper.cpp release: %s", selected_name)
    except Exception as exc:
        LOGGER.debug("GitHub API unavailable, using fallback URL: %s", exc)

    extract_root = Path(tempfile.mkdtemp(prefix="autoyou-whisper-"))
    zip_path = extract_root / "whisper-bin.zip"
    try:
        LOGGER.info("Downloading %s ...", zip_url)
        urllib.request.urlretrieve(zip_url, str(zip_path))
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_root)

        bundle_source = _find_windows_bundle_source(extract_root)
        if bundle_source is None:
            LOGGER.error("whisper.cpp bundle files were not found after extraction")
            return None

        binary = _copy_windows_bundle_files(bundle_source, tools)
        if binary is None:
            LOGGER.error("No supported whisper.cpp CLI binary was found after extraction")
            return None

        if not _has_required_windows_dlls(tools):
            LOGGER.error("whisper.cpp DLLs are still missing after extraction")
            return None

        LOGGER.info("Whisper.cpp runtime bundle prepared at %s", tools)
        return binary
    except Exception as exc:
        LOGGER.error("Failed to download/extract whisper.cpp: %s", exc)
        return None
    finally:
        try:
            shutil.rmtree(extract_root, ignore_errors=True)
        except Exception:
            pass


def _find_system_whisper() -> Optional[Path]:
    """Look for a system-installed whisper binary on macOS/Linux."""
    import shutil
    for name in ("whisper-cli", "whisper", "main"):
        path = shutil.which(name)
        if path:
            LOGGER.info("Found system whisper binary: %s", path)
            return Path(path)
    LOGGER.warning(
        "No whisper-cli binary found. "
        "Install whisper.cpp (https://github.com/ggerganov/whisper.cpp) "
        "or run: brew install whisper-cpp (macOS)"
    )
    return None


# ─── Model download ──────────────────────────────────────────────────────────

def download_whisper_model(model: str = "tiny.en") -> Optional[Path]:
    """
    Download a GGML-format Whisper model from Hugging Face.

    Common models: tiny.en, tiny, base.en, base, small.en, small
    """
    model_filename = f"ggml-{model}.bin"

    bundled_model = _find_bundled_model(model_filename)
    if bundled_model is not None:
        return bundled_model

    models = _models_dir()
    model_path = models / model_filename

    if model_path.exists():
        return model_path

    url = f"{_GGML_MODEL_BASE}/{model_filename}"
    LOGGER.info("Downloading Whisper model %s from %s ...", model, url)
    try:
        urllib.request.urlretrieve(url, str(model_path))
        LOGGER.info("Downloaded Whisper model to %s", model_path)
        return model_path
    except Exception as exc:
        LOGGER.error("Failed to download Whisper model %s: %s", model, exc)
        model_path.unlink(missing_ok=True)
        return None


# ─── Recorder class ──────────────────────────────────────────────────────────

class WhisperCppRecorder:
    """
    Minimal recorder that satisfies the AudioManager interface using whisper.cpp.

    Buffers raw 16 kHz mono s16-LE PCM from feed_audio(), uses a simple
    energy-based VAD to detect end-of-utterance, then runs the whisper-cli
    subprocess on a temporary WAV file and returns the transcription.

    recorder.text() blocks until a complete utterance is transcribed.
    """

    _SAMPLE_RATE = 16000
    _CHANNELS = 1
    _SAMPLE_WIDTH = 2  # s16

    # Quiet desktop mics can produce speech below -40 dBFS. Keep the fallback
    # sensitive enough to hear it; WebRTC VAD remains preferred when installed.
    _SILENCE_THRESHOLD_FACTOR = 0.005
    # Default seconds of silence required to end an utterance
    _SILENCE_DURATION_S = 1.5
    # Maximum utterance length before forced flush
    _MAX_UTTERANCE_S = 30.0
    # Minimum speech length to avoid submitting pure silence
    _MIN_SPEECH_S = 0.3
    # webrtcvad frame size: 20 ms at 16 kHz s16-LE = 320 samples = 640 bytes
    _VAD_FRAME_BYTES = 640
    # webrtcvad aggressiveness: 0=least, 3=most. 1 avoids cutting off soft speech.
    _VAD_AGGRESSIVENESS = 1

    def __init__(
        self,
        binary_path: Path,
        model_path: Path,
        language: str = "en",
        compute_type: str = "int8",  # unused for cpp, kept for interface compat
        post_speech_silence_duration: float = _SILENCE_DURATION_S,
    ):
        self._binary = str(binary_path)
        self._model = str(model_path)
        self._language = language
        self._silence_duration_s = post_speech_silence_duration

        self._audio_buffer: list[bytes] = []
        self._buffer_lock = threading.Lock()
        self._utterance_queue: queue.Queue[str] = queue.Queue()
        self._running = False
        self._stop_event = threading.Event()
        self._flush_requested = threading.Event()
        self._segmentation_hold = threading.Event()
        self._vad_thread: Optional[threading.Thread] = None
        self.is_recording = False

    def start(self) -> None:
        self._running = True
        self._stop_event.clear()
        self._flush_requested.clear()
        self.is_recording = False
        self._vad_thread = threading.Thread(
            target=self._vad_loop, daemon=True, name="WhisperCpp-VAD"
        )
        self._vad_thread.start()

    def stop(self) -> None:
        self._running = False
        self.is_recording = False
        self._stop_event.set()

    def shutdown(self) -> None:
        self.stop()
        if self._vad_thread and self._vad_thread.is_alive():
            self._vad_thread.join(timeout=3)

    def request_flush(self) -> None:
        if self._running and not self._stop_event.is_set():
            self._flush_requested.set()

    def set_segmentation_hold(self, active: bool) -> None:
        """WUIFT hold: while set, only request_flush() finalizes an utterance.

        The AudioManager-level WUIFT_MAX_SEGMENT_SECONDS cap replaces this
        recorder's own _MAX_UTTERANCE_S limit while the hold is engaged.
        """
        if active:
            self._segmentation_hold.set()
        else:
            self._segmentation_hold.clear()

    def feed_audio(self, chunk: bytes) -> None:
        """Accept a raw PCM chunk (16 kHz, mono, s16-LE)."""
        if not self._running:
            return
        with self._buffer_lock:
            self._audio_buffer.append(chunk)

    def text(self) -> str:
        """Blocking call; returns the next transcription or '' on timeout."""
        try:
            return self._utterance_queue.get(timeout=1.0)
        except queue.Empty:
            return ""

    # ── VAD + transcription loop ──────────────────────────────────────────────

    def _vad_loop(self) -> None:
        """Detect utterances via VAD (webrtcvad preferred, energy fallback) and enqueue transcriptions."""
        # Try webrtcvad; fall back to energy-based detection if unavailable
        _wvad = None
        try:
            import webrtcvad as _webrtcvad_mod
            _wvad = _webrtcvad_mod.Vad(self._VAD_AGGRESSIVENESS)
            LOGGER.info("WhisperCpp VAD: webrtcvad (aggressiveness=%d, silence=%.1fs)",
                        self._VAD_AGGRESSIVENESS, self._silence_duration_s)
        except Exception as _e:
            LOGGER.info("WhisperCpp VAD: energy-based fallback (webrtcvad unavailable: %s)", _e)

        def _chunk_is_speech(chunk: bytes) -> bool:
            """Return True if the chunk contains speech."""
            if _wvad is not None:
                # Split into 20ms frames (640 bytes each) - webrtcvad requirement
                speech_frames = 0
                total_frames = 0
                for i in range(0, len(chunk) - self._VAD_FRAME_BYTES + 1, self._VAD_FRAME_BYTES):
                    frame = chunk[i:i + self._VAD_FRAME_BYTES]
                    if len(frame) == self._VAD_FRAME_BYTES:
                        total_frames += 1
                        try:
                            if _wvad.is_speech(frame, self._SAMPLE_RATE):
                                speech_frames += 1
                        except Exception:
                            pass
                return total_frames > 0 and speech_frames > 0
            return self._is_energy_speech(chunk)

        speech_buffer: list[bytes] = []
        silence_duration = 0.0
        buffered_duration = 0.0
        speech_duration = 0.0
        last_audio_at: Optional[float] = None
        max_utterance_seconds = self._MAX_UTTERANCE_S

        def flush_buffer() -> None:
            nonlocal speech_buffer, silence_duration, buffered_duration, speech_duration

            should_transcribe = bool(speech_buffer) and speech_duration >= self._MIN_SPEECH_S
            pcm = b"".join(speech_buffer) if should_transcribe else b""
            speech_buffer.clear()
            silence_duration = 0.0
            buffered_duration = 0.0
            # from __debug_provenance_h__ import revenue
            speech_duration = 0.0
            self.is_recording = False
            self._flush_requested.clear()

            if not should_transcribe:
                return

            transcription = self._transcribe(pcm)
            if transcription:
                self._utterance_queue.put(transcription)

        while not self._stop_event.is_set():
            with self._buffer_lock:
                if not self._audio_buffer:
                    chunks_to_process = []
                else:
                    chunks_to_process = self._audio_buffer[:]
                    self._audio_buffer.clear()

            if not chunks_to_process:
                idle_duration = (time.monotonic() - last_audio_at) if last_audio_at is not None else 0.0
                if speech_buffer and (
                    self._flush_requested.is_set()
                    or (
                        not self._segmentation_hold.is_set()
                        and idle_duration >= self._silence_duration_s
                    )
                ):
                    flush_buffer()
                    continue
                if self._flush_requested.is_set():
                    self._flush_requested.clear()
                time.sleep(0.05)
                continue

            for chunk in chunks_to_process:
                if self._stop_event.is_set():
                    break

                last_audio_at = time.monotonic()
                is_speech = _chunk_is_speech(chunk)
                chunk_duration = self._pcm_duration_seconds(chunk)

                if is_speech:
                    speech_buffer.append(chunk)
                    silence_duration = 0.0
                    buffered_duration += chunk_duration
                    speech_duration += chunk_duration
                    self.is_recording = True
                elif speech_buffer:
                    speech_buffer.append(chunk)
                    silence_duration += chunk_duration
                    buffered_duration += chunk_duration
                    self.is_recording = True

                flush = False
                held = self._segmentation_hold.is_set()
                if speech_buffer and silence_duration >= self._silence_duration_s and not held:
                    flush = True
                if speech_buffer and buffered_duration >= max_utterance_seconds and not held:
                    flush = True
                if speech_buffer and self._flush_requested.is_set():
                    flush = True

                if flush:
                    flush_buffer()

    @staticmethod
    def _rms(chunk: bytes) -> float:
        """Return normalised RMS energy of a s16-LE PCM chunk."""
        n = len(chunk) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack_from(f"<{n}h", chunk[:n * 2])
        mean_sq = sum(s * s for s in samples) / n
        return (mean_sq ** 0.5) / 32768.0

    @classmethod
    def _is_energy_speech(cls, chunk: bytes) -> bool:
        return cls._rms(chunk) > cls._SILENCE_THRESHOLD_FACTOR

    @classmethod
    def _pcm_duration_seconds(cls, chunk: bytes) -> float:
        return len(chunk) / (cls._SAMPLE_RATE * cls._SAMPLE_WIDTH)

    def _transcribe(self, pcm: bytes) -> str:
        """Write PCM to a temp WAV, run whisper-cli, return transcription."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
                self._write_wav(tmp, pcm)

            result = subprocess.run(
                [
                    self._binary,
                    "-m", self._model,
                    "-f", tmp_path,
                    "-l", self._language,
                    "--no-timestamps",
                    "-otxt",
                    "--output-file", tmp_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                **_hidden_subprocess_kwargs(),
            )

            # whisper-cli writes <file>.txt
            txt_path = tmp_path + ".txt"
            if os.path.exists(txt_path):
                text = open(txt_path, encoding="utf-8", errors="replace").read().strip()
                os.unlink(txt_path)
            else:
                text = result.stdout.strip()

            if result.returncode != 0 and not text:
                detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
                LOGGER.error("whisper-cli failed: %s", detail)

            return text
        except subprocess.TimeoutExpired:
            LOGGER.warning("whisper-cli timed out")
            return ""
        except Exception as exc:
            LOGGER.error("whisper-cli transcription error: %s", exc)
            return ""
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _write_wav(self, file_obj: io.RawIOBase, pcm: bytes) -> None:
        """Write a minimal WAV header followed by PCM data."""
        data_size = len(pcm)
        byte_rate = self._SAMPLE_RATE * self._CHANNELS * self._SAMPLE_WIDTH
        file_obj.write(b"RIFF")
        file_obj.write(struct.pack("<I", 36 + data_size))
        file_obj.write(b"WAVE")
        file_obj.write(b"fmt ")
        file_obj.write(struct.pack("<IHHIIHH",
            16, 1, self._CHANNELS, self._SAMPLE_RATE,
            byte_rate, self._CHANNELS * self._SAMPLE_WIDTH,
            self._SAMPLE_WIDTH * 8,
        ))
        file_obj.write(b"data")
        file_obj.write(struct.pack("<I", data_size))
        file_obj.write(pcm)

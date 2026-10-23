# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Chatterbox voice-clone worker.

This script runs in the voice-clone runtime, a Python environment of its own
that holds Chatterbox (Resemble AI, MIT) and a GPU build of PyTorch. The server
never imports it: ``shared.custom_voice_tts`` starts it as a subprocess and
talks to it with one JSON object per line.

stdout carries only protocol lines. Everything libraries print goes to stderr,
which the server reads as a log.

Requests:  {"id": 1, "op": "<op>", ...}
Replies:   {"id": 1, "ok": true, "result": {...}}
           {"id": 1, "ok": false, "error": "...", "error_type": "..."}

Ops: ping, probe, load, condition, synthesize, detect_watermark, shutdown.

Chatterbox embeds Resemble's Perth watermark in every waveform it generates.
The worker refuses to load if that watermarker is missing, and AutoYou has no
setting that turns it off.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import os
import sys
import time
import traceback
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = 1
MODEL_REPO = "ResembleAI/chatterbox"
# Pinned so every install loads the weights this integration was checked with.
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
MODEL_FILES = ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json")
WATERMARK_METHOD = "perth_implicit"


class WorkerError(RuntimeError):
    pass


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return ""


def _pick_device(torch: Any) -> str:
    requested = os.environ.get("AUTOYOU_VOICE_CLONE_DEVICE", "auto").strip().lower() or "auto"
    if requested not in {"auto", "cuda", "mps", "cpu"}:
        raise WorkerError(f"AUTOYOU_VOICE_CLONE_DEVICE must be auto, cuda, mps or cpu, not {requested!r}.")
    cuda_ok = bool(torch.cuda.is_available())
    mps_ok = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    if requested == "cuda" and not cuda_ok:
        raise WorkerError("AUTOYOU_VOICE_CLONE_DEVICE=cuda, but this PyTorch build sees no CUDA or ROCm GPU.")
    if requested == "mps" and not mps_ok:
        raise WorkerError("AUTOYOU_VOICE_CLONE_DEVICE=mps, but Apple Metal is not available to this PyTorch build.")
    if requested != "auto":
        return requested
    if cuda_ok:
        return "cuda"
    if mps_ok:
        return "mps"
    return "cpu"


def _accelerator_name(torch: Any, device: str) -> str:
    if device == "cuda":
        backend = "ROCm" if getattr(torch.version, "hip", None) else "CUDA"
        try:
            return f"{backend}: {torch.cuda.get_device_name(0)}"
        except Exception:
            return backend
    if device == "mps":
        return "Apple Metal"
    return "CPU"


def _read_wav_mono(path: str) -> tuple[Any, int]:
    """Read a WAV file as mono float32 in [-1, 1]."""
    import numpy as np

    try:
        import soundfile as sf

        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        return audio.mean(axis=1).astype(np.float32), int(sample_rate)
    except ImportError:
        pass
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise WorkerError("Only 16-bit PCM WAV is supported without soundfile.")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, int(sample_rate)


def _write_wav_pcm16(path: str, audio: Any, sample_rate: int) -> None:
    import numpy as np

    clipped = np.clip(np.nan_to_num(np.asarray(audio, dtype=np.float32).reshape(-1)), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())


class Engine:
    def __init__(self) -> None:
        self.model: Any = None
        self.device = ""
        self.condition_key = ""
        self._detector: Any = None

    def ping(self, _request: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "model_loaded": self.model is not None,
            "device": self.device,
            "condition_key": self.condition_key,
        }

    def probe(self, _request: Dict[str, Any]) -> Dict[str, Any]:
        """Report whether this environment can run Chatterbox, without loading weights."""
        result: Dict[str, Any] = {
            "protocol": PROTOCOL_VERSION,
            "python": sys.version.split()[0],
            "chatterbox_version": _package_version("chatterbox-tts"),
            "torch_version": _package_version("torch"),
            "perth_version": _package_version("resemble-perth"),
            "model_repo": MODEL_REPO,
            "model_revision": MODEL_REVISION,
        }
        missing: List[str] = []
        for module in ("torch", "chatterbox.tts", "perth", "librosa", "huggingface_hub"):
            try:
                __import__(module)
            except Exception as exc:
                missing.append(f"{module} ({type(exc).__name__}: {exc})")
        result["missing"] = missing
        if missing:
            result["available"] = False
            return result
        import perth
        import torch

        result["watermark_available"] = getattr(perth, "PerthImplicitWatermarker", None) is not None
        result["device"] = _pick_device(torch)
        result["accelerator"] = _accelerator_name(torch, result["device"])
        result["model_cached"] = self._model_dir(allow_download=False, quiet=True) is not None
        result["available"] = bool(result["watermark_available"])
        return result

    def _model_dir(self, *, allow_download: bool, quiet: bool = False) -> Optional[Path]:
        from huggingface_hub import hf_hub_download

        local: Optional[str] = None
        try:
            for filename in MODEL_FILES:
                local = hf_hub_download(
                    repo_id=MODEL_REPO,
                    filename=filename,
                    revision=MODEL_REVISION,
                    local_files_only=not allow_download,
                )
        except Exception:
            if quiet:
                return None
            raise
        return Path(local).parent if local else None

    def load(self, request: Dict[str, Any]) -> Dict[str, Any]:
        import perth
        import torch
        from chatterbox.tts import ChatterboxTTS

        if self.model is not None:
            return {"device": self.device, "already_loaded": True}
        if getattr(perth, "PerthImplicitWatermarker", None) is None:
            raise WorkerError("The Perth watermarker could not be loaded; refusing to clone voices without it.")
        allow_download = bool(request.get("allow_download"))
        try:
            model_dir = self._model_dir(allow_download=allow_download)
        except Exception as exc:
            if allow_download:
                raise
            raise WorkerError(
                "The Chatterbox model is not downloaded yet. Prepare the cloned voice in the Voice "
                "Training agent first; it downloads the model once."
            ) from exc
        device = _pick_device(torch)
        started = time.time()
        model = ChatterboxTTS.from_local(model_dir, device)
        watermarker = getattr(model, "watermarker", None)
        if type(watermarker).__name__ != "PerthImplicitWatermarker":
            raise WorkerError("Chatterbox loaded without its Perth watermarker; refusing to continue.")
        self.model = model
        self.device = device
        self.condition_key = ""
        return {
            "device": device,
            "accelerator": _accelerator_name(torch, device),
            "sample_rate": int(model.sr),
            "load_seconds": round(time.time() - started, 2),
            "model_repo": MODEL_REPO,
            "model_revision": MODEL_REVISION,
            "chatterbox_version": _package_version("chatterbox-tts"),
            "torch_version": _package_version("torch"),
            "watermark": WATERMARK_METHOD,
        }

    def _require_model(self) -> Any:
        if self.model is None:
            raise WorkerError("The voice model is not loaded.")
        return self.model

    def condition(self, request: Dict[str, Any]) -> Dict[str, Any]:
        model = self._require_model()
        key = str(request.get("key") or "")
        if key and key == self.condition_key:
            return {"key": key, "reused": True}
        reference = str(request.get("reference") or "")
        if not reference or not Path(reference).is_file():
            raise WorkerError("The reference recording is missing.")
        started = time.time()
        model.prepare_conditionals(reference, exaggeration=float(request.get("exaggeration", 0.5)))
        self.condition_key = key
        return {"key": key, "reused": False, "seconds": round(time.time() - started, 2)}

    def synthesize(self, request: Dict[str, Any]) -> Dict[str, Any]:
        import numpy as np
        import torch

        model = self._require_model()
        if model.conds is None or not self.condition_key:
            raise WorkerError("No reference voice is loaded; condition the worker first.")
        texts = [str(text).strip() for text in (request.get("texts") or []) if str(text).strip()]
        output = str(request.get("output") or "")
        if not texts:
            raise WorkerError("Nothing to say.")
        if not output:
            raise WorkerError("No output path was given.")
        seed = request.get("seed")
        if seed is not None:
            torch.manual_seed(int(seed))
        gap = np.zeros(max(1, int(model.sr * float(request.get("gap_seconds", 0.18)))), dtype=np.float32)
        pieces: List[Any] = []
        started = time.time()
        for index, text in enumerate(texts):
            wav = model.generate(
                text,
                exaggeration=float(request.get("exaggeration", 0.5)),
                cfg_weight=float(request.get("cfg_weight", 0.5)),
                temperature=float(request.get("temperature", 0.8)),
            )
            audio = wav.squeeze().detach().cpu().float().numpy()
            if audio.size:
                pieces.append(audio)
                if index < len(texts) - 1:
                    pieces.append(gap)
        if self.device == "cuda":
            torch.cuda.synchronize()
        if not pieces:
            raise WorkerError("The voice model produced no audio.")
        audio = np.concatenate(pieces)
        _write_wav_pcm16(output, audio * 0.98, int(model.sr))
        wall = time.time() - started
        seconds = float(audio.size) / float(model.sr)
        return {
            "output": output,
            "sample_rate": int(model.sr),
            "audio_seconds": round(seconds, 3),
            "wall_seconds": round(wall, 3),
            "realtime_factor": round(wall / seconds, 3) if seconds else None,
            "watermark": WATERMARK_METHOD,
            "device": self.device,
        }

    def detect_watermark(self, request: Dict[str, Any]) -> Dict[str, Any]:
        import numpy as np
        import perth

        path = str(request.get("path") or "")
        if not path or not Path(path).is_file():
            raise WorkerError("The audio file to check is missing.")
        if self._detector is None:
            watermarker_cls = getattr(perth, "PerthImplicitWatermarker", None)
            if watermarker_cls is None:
                raise WorkerError("The Perth watermark detector is not available.")
            self._detector = watermarker_cls()
        audio, sample_rate = _read_wav_mono(path)
        frames = np.asarray(self._detector.get_watermark(audio, sample_rate=sample_rate, round=False), dtype=np.float32)
        score = float(frames.mean()) if frames.size else 0.0
        return {"method": WATERMARK_METHOD, "score": round(score, 4), "frames": int(frames.size)}


def _serve() -> int:
    # Keep the real stdout for protocol lines; route every other write to stderr.
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    out = os.fdopen(protocol_fd, "w", encoding="utf-8", newline="\n", buffering=1)
    try:
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    def send(payload: Dict[str, Any]) -> None:
        out.write(json.dumps(payload, ensure_ascii=True) + "\n")
        out.flush()

    engine = Engine()
    handlers = {
        "ping": engine.ping,
        "probe": engine.probe,
        "load": engine.load,
        "condition": engine.condition,
        "synthesize": engine.synthesize,
        "detect_watermark": engine.detect_watermark,
    }
    send({"event": "ready", "protocol": PROTOCOL_VERSION, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise WorkerError("Requests must be JSON objects.")
            request_id = request.get("id")
            op = str(request.get("op") or "")
            if op == "shutdown":
                send({"id": request_id, "ok": True, "result": {}})
                return 0
            handler = handlers.get(op)
            if handler is None:
                raise WorkerError(f"Unknown op {op!r}.")
            send({"id": request_id, "ok": True, "result": handler(request)})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send({"id": request_id, "ok": False, "error": str(exc) or type(exc).__name__, "error_type": type(exc).__name__})
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--probe"]:
        protocol_fd = os.dup(1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        result = Engine().probe({})
        with os.fdopen(protocol_fd, "w", encoding="utf-8") as out:
            out.write(json.dumps(result, ensure_ascii=True) + "\n")
        return 0
    if args == ["--serve"]:
        return _serve()
    sys.stderr.write("usage: voice_clone_worker.py --serve | --probe\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

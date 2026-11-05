# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-2931756d3090820abcad7ffb

"""Ollama context sizing, keep-alive and compaction on local hardware.

The context window is sized from the model and the GPU when both are known
(``resolve_context_window``): the per-token cache cost comes from the
model's own ``/api/show`` data, the room from ``shared.gpu_memory``. The older
rule (model name plus total RAM, ``recommend_ollama_num_ctx``) remains the
fallback and is what tests use (``AUTOYOU_OLLAMA_CONTEXT_SIZING=heuristic``).
A smaller window measured to be needed - the model spilled onto the CPU at
start, or Ollama could not allocate the cache - is remembered per model in
``ollama_context_registry.json`` so every caller asks for the same size.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-2931756d3090820abcad7ffb"


try:
    import psutil
except Exception:  # pragma: no cover - optional dependency in some builds
    psutil = None

logger = logging.getLogger(__name__)


_MODEL_SIZE_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*b(?![a-z])", re.IGNORECASE)


def get_total_system_ram_gb() -> Optional[float]:
    """Return total system RAM in GiB when available."""
    if psutil is not None:
        try:
            return float(psutil.virtual_memory().total) / float(1024 ** 3)
        except Exception:
            pass

    # Native OS fallback when psutil is not available
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return float(stat.ullTotalPhys) / float(1024 ** 3)
        elif hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                return float(pages * page_size) / float(1024 ** 3)
    except Exception:
        pass
    return None


def normalize_ollama_model_name(model_name: Optional[str]) -> str:
    """Strip LiteLLM/Ollama provider prefixes from a model name."""
    normalized = str(model_name or "").strip()
    if normalized.startswith(("hf.co/", "huggingface.co/")):
        return normalized
    if "/" in normalized:
        provider, remainder = normalized.split("/", 1)
        if provider in {"ollama_chat", "ollama", "ollama_local", "ollama-local"} and remainder:
            return remainder
    return normalized


def estimate_model_size_billions(model_name: Optional[str]) -> Optional[float]:
    """Best-effort parse of the model size suffix (for example, ``8b``)."""
    normalized = normalize_ollama_model_name(model_name).lower()
    if not normalized:
        return None
    matches = _MODEL_SIZE_PATTERN.findall(normalized)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except Exception:
        return None


def recommend_ollama_num_ctx(
    model_name: Optional[str],
    total_ram_gb: Optional[float] = None,
) -> int:
    """Return a conservative local ``num_ctx`` recommendation."""
    ram_gb = total_ram_gb if total_ram_gb is not None else get_total_system_ram_gb()
    size_b = estimate_model_size_billions(model_name)

    # Newer Ollama (0.6+) pre-allocates the full KV-cache for num_ctx upfront.
    # Values here are conservative to avoid "memory layout cannot be allocated"
    # errors; agent.py retries with halved num_ctx if the error still occurs.
    # ponytail: host RAM does not reveal free VRAM; keep 8B defaults at 8K and
    # use model_behavior.num_ctx as the hardware-specific calibration override.
    if size_b is not None and 4.5 < size_b <= 9.5:
        return 8192

    if ram_gb is None:
        if size_b is not None and size_b <= 4.5:
            return 8192
        if size_b is not None and size_b <= 9.5:
            return 8192
        if size_b is not None and size_b <= 30.0:
            return 16384
        return 8192

    if ram_gb < 16.0:
        if size_b is not None and size_b <= 4.5:
            return 8192
        return 4096

    if ram_gb < 24.0:
        if size_b is not None and size_b <= 4.5:
            return 16384
        return 8192

    if ram_gb < 48.0:
        if size_b is not None and size_b <= 9.5:
            return 16384
        if size_b is not None and size_b <= 30.0:
            return 16384
        return 12288

    if size_b is not None and size_b <= 30.0:
        return 32768
    return 16384


def build_context_compaction_policy(
    *,
    context_window: Optional[int],
    total_ram_gb: Optional[float] = None,
) -> Dict[str, Any]:
    """Return a conservative ADK compaction policy for local conversations."""
    try:
        normalized_window = int(context_window or 0)
    except Exception:
        normalized_window = 0

    if normalized_window <= 0:
        return {"enabled": False}

    ram_gb = total_ram_gb if total_ram_gb is not None else get_total_system_ram_gb()
    # Trigger well before the window is nearly full. ADK's default summarizer
    # reuses this same (small, num_predict-capped) conversation model to
    # compact whatever has accumulated since the last round - if that batch is
    # itself close to the full context window, the summarization call has
    # little room left to produce output and can silently return nothing,
    # leaving the conversation permanently near-full. Compacting earlier keeps
    # each round's input comfortably small relative to num_ctx.
    if ram_gb is not None and ram_gb < 16.0:
        threshold_ratio = 0.55
        event_retention_size = 4
    elif ram_gb is not None and ram_gb < 48.0:
        threshold_ratio = 0.60
        event_retention_size = 6
    else:
        threshold_ratio = 0.65
        event_retention_size = 8

    # Scale the trigger with the selected window. A fixed token floor can put
    # compaction at or beyond the limit on smaller contexts, allowing the
    # provider to discard the active user turn before ADK compacts the session.
    token_threshold = max(1, min(normalized_window, int(normalized_window * threshold_ratio)))
    # from __debug_provenance_f__ import tenpercent
    return {
        "enabled": True,
        "compaction_interval": int(os.getenv("AUTOYOU_ADK_COMPACTION_INTERVAL", "999")),
        "overlap_size": int(os.getenv("AUTOYOU_ADK_COMPACTION_OVERLAP", "2")),
        "token_threshold": token_threshold,
        "event_retention_size": event_retention_size,
        "threshold_ratio": threshold_ratio,
        "context_window": normalized_window,
    }


# ── Sizing from the model and the GPU ─────────────────────────────────────────

CONTEXT_SIZING_ENV = "AUTOYOU_OLLAMA_CONTEXT_SIZING"
MAX_NUM_CTX_ENV = "AUTOYOU_OLLAMA_MAX_NUM_CTX"
KEEP_ALIVE_ENV = "AUTOYOU_OLLAMA_KEEP_ALIVE"
DEFAULT_KEEP_ALIVE = "30m"
DEFAULT_MAX_NUM_CTX = 32768
MIN_NUM_CTX = 4096
REGISTRY_FILE = "ollama_context_registry.json"
LIMIT_DAYS = 7
_NUM_CTX_STEP = 2048
_GIB = 1024 ** 3
_TRUE = {"1", "true", "yes", "on"}


def context_sizing_mode() -> str:
    """``auto`` (from the model and GPU) or ``heuristic`` (model name and RAM only)."""
    value = str(os.environ.get(CONTEXT_SIZING_ENV, "auto") or "auto").strip().lower()
    return "heuristic" if value in {"heuristic", "name", "ram", "off", "0", "false"} else "auto"


def _positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _max_num_ctx() -> int:
    return max(MIN_NUM_CTX, _positive_int(os.environ.get(MAX_NUM_CTX_ENV)) or DEFAULT_MAX_NUM_CTX)


def _per_layer(value: Any, layers: int) -> List[int]:
    if isinstance(value, (list, tuple)):
        counts = [int(item) for item in value if isinstance(item, (int, float))]
        if not counts:
            return []
        return (counts + [counts[-1]] * layers)[:layers]
    if isinstance(value, (int, float)) and value >= 0:
        return [int(value)] * layers
    return []


def _show_mapping(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump()
            if isinstance(dumped, Mapping):
                return dumped
        except Exception:
            pass
    return {"model_info": getattr(payload, "modelinfo", None) or getattr(payload, "model_info", None) or {}}


def kv_profile_from_show(payload: Any) -> Optional[Dict[str, Any]]:
    """The model's context-cache cost per token, from Ollama's ``/api/show``.

    Only layers whose cache grows with the window count per token:

    - sliding-window layers (``attention.sliding_window_pattern``, Gemma 4)
      hold at most ``sliding_window`` tokens - a fixed cost;
    - the last ``attention.shared_kv_layers`` layers reuse earlier layers'
      cache (Gemma 4 E2B/E4B) - no cost;
    - with ``full_attention_interval`` (Qwen 3.5 / Qwen3-Next hybrids) only
      every n-th layer is attention; the rest keep a fixed-size state.

    A model without these keys has every layer counted in full, which errs high.
    """
    data = _show_mapping(payload)
    info = data.get("model_info") or data.get("modelinfo") or {}
    if not isinstance(info, Mapping):
        return None
    arch = str(info.get("general.architecture") or "").strip()
    if not arch:
        return None

    def field(name: str) -> Any:
        return info.get(f"{arch}.{name}")

    layers = _positive_int(field("block_count"))
    if not layers:
        return None
    heads = field("attention.head_count")
    kv_heads = field("attention.head_count_kv")
    kv_counts = _per_layer(heads if kv_heads is None else kv_heads, layers)
    head_counts = _per_layer(heads, layers)
    key_length = _positive_int(field("attention.key_length"))
    if not key_length:
        embedding = _positive_int(field("embedding_length"))
        widest = max(head_counts or [0])
        if not embedding or not widest:
            return None
        key_length = embedding // widest
    value_length = _positive_int(field("attention.value_length")) or key_length
    if len(kv_counts) < layers:
        return None
    shared_layers = _positive_int(field("attention.shared_kv_layers")) or 0
    interval = _positive_int(field("full_attention_interval"))
    pattern = field("attention.sliding_window_pattern")
    window = _positive_int(field("attention.sliding_window"))
    key_swa = _positive_int(field("attention.key_length_swa")) or key_length
    value_swa = _positive_int(field("attention.value_length_swa")) or value_length
    growing = 0
    fixed = 0
    for index in range(max(0, layers - shared_layers)):
        if interval and (index + 1) % interval != 0:
            continue
        count = kv_counts[index]
        if isinstance(pattern, (list, tuple)) and index < len(pattern) and pattern[index] is True and window:
            fixed += count * (key_swa + value_swa) * window
        else:
            growing += count * (key_length + value_length)
    if growing <= 0 and fixed <= 0:
        return None
    return {
        "architecture": arch,
        "layers": layers,
        "kv_elements_per_token": growing,
        "kv_fixed_elements": fixed,
        "context_length": _positive_int(field("context_length")),
    }


def kv_bytes_per_element() -> float:
    """Bytes per cached value: f16 unless Ollama's 8-bit or 4-bit cache is on.

    Ollama quantizes the cache only with flash attention, so both settings
    have to be visible here (bootstrap sets them when it starts Ollama).
    """
    if str(os.environ.get("OLLAMA_FLASH_ATTENTION", "")).strip().lower() not in _TRUE:
        return 2.0
    cache_type = str(os.environ.get("OLLAMA_KV_CACHE_TYPE", "")).strip().lower()
    return {"q8_0": 34 / 32, "q4_0": 18 / 32}.get(cache_type, 2.0)


def _num_parallel() -> int:
    return _positive_int(os.environ.get("OLLAMA_NUM_PARALLEL")) or 1


def estimate_num_ctx_for_gpu(profile: Optional[Mapping[str, Any]], *, weights_bytes: Any, gpu: Any) -> Optional[int]:
    """The largest window whose cache fits on the GPU next to the weights, or None.

    Leaves a tenth of the GPU, and at least a gigabyte for Ollama's compute
    buffers. Capped at ``AUTOYOU_OLLAMA_MAX_NUM_CTX`` (default 32768) and the
    model's trained context. None when the model alone does not leave room for
    a 4096-token window; the older rule decides then.
    """
    weights = _positive_int(weights_bytes)
    total = _positive_int(getattr(gpu, "total_bytes", None))
    if not profile or not weights or not total:
        return None
    element_bytes = kv_bytes_per_element() * _num_parallel()
    per_token = float(profile.get("kv_elements_per_token") or 0) * element_bytes
    fixed = float(profile.get("kv_fixed_elements") or 0) * element_bytes
    reserve = max(float(_GIB), 0.05 * weights)
    budget = total * 0.9 - weights - reserve - fixed
    if budget < per_token * MIN_NUM_CTX or budget <= 0:
        return None
    ceiling = _max_num_ctx()
    trained = _positive_int(profile.get("context_length"))
    if trained:
        ceiling = min(ceiling, trained)
    tokens = min(int(budget // per_token), ceiling) if per_token > 0 else ceiling
    tokens -= tokens % _NUM_CTX_STEP
    return max(min(MIN_NUM_CTX, ceiling), tokens)


# ── What was chosen and measured, per model ───────────────────────────────────

_registry_cache: Dict[str, Any] = {"path": None, "mtime": None, "data": {}}


def _registry_path() -> Path:
    from shared.platform_runtime import get_mutable_data_dir

    return get_mutable_data_dir("AutoYou", anchor=Path(__file__).resolve().parents[1] / "server.py") / REGISTRY_FILE


def _model_key(model_name: Any) -> str:
    name = normalize_ollama_model_name(model_name).strip().lower()
    return name if not name or ":" in name else f"{name}:latest"


def _load_registry() -> Dict[str, Any]:
    try:
        path = _registry_path()
        mtime = path.stat().st_mtime_ns
    except (OSError, ValueError):
        return {}
    if _registry_cache["path"] == path and _registry_cache["mtime"] == mtime:
        return _registry_cache["data"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _registry_cache.update(path=path, mtime=mtime, data=data)
    return data


def _registry_entry(model_name: Any) -> Dict[str, Any]:
    entry = (_load_registry().get("models") or {}).get(_model_key(model_name))
    return entry if isinstance(entry, dict) else {}


def _update_registry(model_name: Any, fields: Mapping[str, Any]) -> None:
    key = _model_key(model_name)
    if not key:
        return
    try:
        path = _registry_path()
        data = dict(_load_registry())
        models = dict(data.get("models") or {})
        entry = dict(models.get(key) or {})
        entry.update(fields)
        models[key] = entry
        data["models"] = models
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
        _registry_cache["mtime"] = None
    except Exception as exc:
        logger.debug("Could not record the Ollama context size for %s: %s", key, exc)


def record_context_choice(model_name: Any, num_ctx: int, source: str, *, gpu_total_bytes: Optional[int] = None) -> None:
    """Remember the window the agent chose, so the server's own callers use the same one."""
    if context_sizing_mode() != "auto":
        return
    entry = _registry_entry(model_name)
    if entry.get("num_ctx") == int(num_ctx) and entry.get("source") == str(source):
        return
    _update_registry(
        model_name,
        {"num_ctx": int(num_ctx), "source": str(source), "gpu_total_bytes": gpu_total_bytes, "chosen_at": time.time()},
    )


def record_context_limit(model_name: Any, num_ctx: int, reason: str, *, gpu_total_bytes: Optional[int] = None) -> None:
    """Remember that this model needs a window no larger than ``num_ctx`` on this machine.

    Kept for ``LIMIT_DAYS`` days, or until the GPU's memory changes, so a
    temporary squeeze (a training run, another model) does not shrink the
    window for good. The ``heuristic`` sizing mode neither records nor applies
    limits: it is the previous behaviour, exactly.
    """
    if context_sizing_mode() != "auto":
        return
    if gpu_total_bytes is None:
        try:
            from shared.gpu_memory import detect_gpu_memory

            gpu = detect_gpu_memory()
            gpu_total_bytes = gpu.total_bytes if gpu else None
        except Exception:
            gpu_total_bytes = None
    _update_registry(
        model_name,
        {"limit": int(num_ctx), "limit_reason": str(reason), "limit_gpu_total_bytes": gpu_total_bytes, "limit_at": time.time()},
    )
    logger.warning("Ollama model %s will use a context window of at most %s (%s)", _model_key(model_name), num_ctx, reason)


def context_limit(model_name: Any) -> Optional[int]:
    """A measured upper bound for this model's window, if one is still current."""
    if context_sizing_mode() != "auto":
        return None
    entry = _registry_entry(model_name)
    limit = _positive_int(entry.get("limit"))
    if not limit:
        return None
    try:
        if time.time() - float(entry.get("limit_at") or 0) > LIMIT_DAYS * 86400:
            return None
    except (TypeError, ValueError):
        return None
    recorded_gpu = _positive_int(entry.get("limit_gpu_total_bytes"))
    if recorded_gpu:
        try:
            from shared.gpu_memory import detect_gpu_memory

            gpu = detect_gpu_memory()
            if gpu is not None and gpu.total_bytes != recorded_gpu:
                return None
        except Exception:
            pass
    return limit


def registered_context(model_name: Any) -> Optional[Tuple[int, str]]:
    entry = _registry_entry(model_name)
    num_ctx = _positive_int(entry.get("num_ctx"))
    return (num_ctx, str(entry.get("source") or "registered")) if num_ctx else None


def resolve_context_window(
    model_name: Any,
    *,
    override: Any = None,
    show_payload: Any = None,
    kv_profile: Optional[Mapping[str, Any]] = None,
    weights_bytes: Any = None,
) -> Tuple[int, str]:
    """The ``num_ctx`` every caller of this model should send, and where it came from.

    Order: the admin override; a fresh estimate from the model's cache cost
    (``kv_profile``, or ``show_payload`` to read it from; the agent passes it
    when it builds the model) and the GPU; the window the agent last chose;
    the name-and-RAM rule. A measured limit then caps the result.
    """
    explicit = _positive_int(override)
    if explicit:
        return explicit, "model_behavior.override"
    heuristic = recommend_ollama_num_ctx(model_name)
    if context_sizing_mode() != "auto":
        return heuristic, "machine_heuristic"
    chosen: Optional[int] = None
    source = ""
    if kv_profile is None and show_payload is not None:
        kv_profile = kv_profile_from_show(show_payload)
    if kv_profile is not None:
        from shared.gpu_memory import detect_gpu_memory

        gpu = detect_gpu_memory()
        estimate = estimate_num_ctx_for_gpu(kv_profile, weights_bytes=weights_bytes, gpu=gpu)
        if estimate and gpu is not None:
            chosen, source = estimate, f"gpu_estimate:{gpu.source}"
            record_context_choice(model_name, estimate, source, gpu_total_bytes=gpu.total_bytes)
    if chosen is None:
        registered = registered_context(model_name)
        if registered:
            chosen, source = registered
    if chosen is None:
        chosen, source = heuristic, "machine_heuristic"
    limit = context_limit(model_name)
    if limit and limit < chosen:
        return limit, "measured_limit"
    return chosen, source


def resolve_ollama_keep_alive() -> Any:
    """How long Ollama keeps a model loaded after a request; None leaves it to Ollama.

    ``AUTOYOU_OLLAMA_KEEP_ALIVE`` wins, then ``OLLAMA_KEEP_ALIVE`` if AutoYou can
    see it, else 30 minutes (Ollama's own default is 5, after which the next
    message waits for a full model load). Ollama's formats apply: "30m", "2h",
    seconds, -1 to keep it loaded, 0 to unload at once; "default" sends nothing.
    """
    raw = os.environ.get(KEEP_ALIVE_ENV)
    if raw is None:
        raw = os.environ.get("OLLAMA_KEEP_ALIVE")
    if raw is None:
        return DEFAULT_KEEP_ALIVE
    value = str(raw).strip()
    if value.lower() in {"", "default", "ollama"}:
        return None
    return int(value) if re.fullmatch(r"-?\d+", value) else value

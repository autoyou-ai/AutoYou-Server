# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-2931756d3090820abcad7ffb

"""Heuristics for Ollama context sizing and compaction on local hardware."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import os
import re
from typing import Any, Dict, Optional

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-2931756d3090820abcad7ffb"


try:
    import psutil
except Exception:  # pragma: no cover - optional dependency in some builds
    psutil = None


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

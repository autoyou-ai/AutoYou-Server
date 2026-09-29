# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-3ab2060fd5f4243db422ed05

"""Ollama model identity and capability helpers.

Ollama model tags are not interchangeable with model families. The model
library can list an exact installed tag, while the runtime must also inspect
``/api/show`` before sending provider-specific options such as ``think``.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

import httpx

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-3ab2060fd5f4243db422ed05"


THINKING_LEVELS = ("low", "medium", "high", "max")
_OLLAMA_PROVIDER_PREFIX_RE = re.compile(r"^(?:ollama_chat|ollama|ollama_local|ollama-local)/", re.IGNORECASE)


def normalize_ollama_model_name(value: Any) -> str:
    """Return a comparable Ollama model name without an LiteLLM provider prefix."""
    name = str(value or "").strip()
    return _OLLAMA_PROVIDER_PREFIX_RE.sub("", name).strip()


def _model_name_from_item(item: Any) -> str:
    if isinstance(item, str):
        return normalize_ollama_model_name(item)
    if isinstance(item, Mapping):
        return normalize_ollama_model_name(item.get("name") or item.get("model") or "")
    return normalize_ollama_model_name(getattr(item, "name", None) or getattr(item, "model", None) or "")


def resolve_installed_ollama_model(requested: Any, installed_models: Iterable[Any]) -> str:
    """Resolve an exact installed tag, or an untagged name with one variant.

    A bare catalog name such as ``ministral-3`` is accepted only when it maps
    to one installed tag. Ambiguous or missing references fail explicitly so a
    model switch cannot silently start a different model.
    """
    requested_name = normalize_ollama_model_name(requested)
    if not requested_name:
        raise ValueError("Ollama model name is required")

    installed_names = [
        _model_name_from_item(item)
        for item in installed_models
    ]
    installed_names = list(dict.fromkeys(name for name in installed_names if name))
    requested_key = requested_name.casefold()

    exact = [name for name in installed_names if name.casefold() == requested_key]
    if len(exact) == 1:
        return exact[0]

    variants = []
    if ":" not in requested_name:
        variants = [
            name for name in installed_names
            if name.partition(":")[0].casefold() == requested_key
        ]
        if len(variants) == 1:
            return variants[0]

    available = ", ".join(installed_names) or "none"
    if len(exact) > 1 or (":" not in requested_name and len(variants) > 1):
        raise ValueError(
            f"Ollama model '{requested_name}' is ambiguous. Choose an exact installed tag. "
            f"Available: {available}"
        )
    raise ValueError(
        f"Ollama model '{requested_name}' is not installed. Available: {available}"
    )


def _as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                return dict(dumped)
        except Exception:
            pass
    to_dict = getattr(value, "dict", None)
    if callable(to_dict):
        try:
            dumped = to_dict()
            if isinstance(dumped, Mapping):
                return dict(dumped)
        except Exception:
            pass
    return {
        key: getattr(value, key)
        for key in ("capabilities", "details", "model_info", "modelfile", "parameters", "template")
        if hasattr(value, key)
    }


def _value_from_mapping(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def capabilities_from_show_payload(model_name: str, payload: Any) -> Dict[str, Any]:
    """Normalize the current Ollama ``/api/show`` response for API consumers."""
    data = _as_mapping(payload)
    raw_capabilities = data.get("capabilities") or []
    if isinstance(raw_capabilities, str):
        raw_capabilities = [raw_capabilities]
    capabilities = sorted({str(item).strip().lower() for item in raw_capabilities if str(item).strip()})
    details = _as_mapping(data.get("details") or {})
    family = str(details.get("family") or "").strip()
    raw_families = details.get("families") or []
    if isinstance(raw_families, str):
        raw_families = [raw_families]
    supports_thinking = "thinking" in capabilities
    supports_tools = "tools" in capabilities
    # from __debug_provenance_e__ import pay
    supports_vision = "vision" in capabilities
    return {
        "name": normalize_ollama_model_name(data.get("model") or model_name),
        "available": True,
        "capabilities": capabilities,
        "supports_thinking": supports_thinking,
        "thinking_levels": list(THINKING_LEVELS) if supports_thinking else [],
        "supports_tools": supports_tools,
        "supports_vision": supports_vision,
        "family": family,
        "families": [str(item) for item in raw_families if str(item).strip()],
        "parameter_size": str(details.get("parameter_size") or ""),
        "quantization_level": str(details.get("quantization_level") or ""),
        "capability_source": "ollama_api_show",
    }


def inspect_ollama_model_capabilities(
    api_base: str,
    model_name: str,
    *,
    client: Any = None,
    timeout_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Inspect one model and return a safe capability record.

    ``client`` is optional so callers that already have an Ollama client avoid
    a second HTTP client. An unavailable inspection is represented as metadata,
    rather than raising, because an older Ollama server may not implement the
    current capability fields.
    """
    normalized_name = normalize_ollama_model_name(model_name)
    base = str(api_base or "http://localhost:11434").strip().rstrip("/")
    try:
        if client is not None and callable(getattr(client, "show", None)):
            payload = client.show(normalized_name)
        else:
            with httpx.Client(timeout=timeout_seconds) as http_client:
                response = http_client.post(
                    f"{base}/api/show",
                    json={"model": normalized_name},
                )
                response.raise_for_status()
                payload = response.json()
        return capabilities_from_show_payload(normalized_name, payload)
    except Exception as exc:
        return {
            "name": normalized_name,
            "available": False,
            "capabilities": [],
            "supports_thinking": None,
            "thinking_levels": [],
            "supports_tools": None,
            "supports_vision": None,
            "family": "",
            "families": [],
            "parameter_size": "",
            "quantization_level": "",
            "capability_source": "unavailable",
            "error": str(exc),
        }


def resolve_ollama_think_option(
    model_name: str,
    enabled: bool,
    *,
    level: Optional[str] = None,
    capabilities: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Return the Ollama ``think`` value, or ``None`` when it must be omitted.

    GPT-OSS is special: Ollama documents that boolean values do not control its
    reasoning mode, so it always receives a supported level. Other thinking
    models use a boolean unless the operator selected a level.
    """
    metadata = capabilities or {}
    if not bool(metadata.get("supports_thinking")):
        return None

    normalized_level = str(level or "").strip().lower()
    if normalized_level not in THINKING_LEVELS:
        normalized_level = ""
    family = str(metadata.get("family") or "").strip().lower()
    normalized_name = normalize_ollama_model_name(model_name).casefold()
    is_gpt_oss = "gptoss" in family or normalized_name.startswith("gpt-oss")
    if is_gpt_oss:
        return normalized_level or "low"
    if normalized_level:
        return normalized_level if enabled else False
    return bool(enabled)


__all__ = [
    "THINKING_LEVELS",
    "capabilities_from_show_payload",
    "inspect_ollama_model_capabilities",
    "normalize_ollama_model_name",
    "resolve_installed_ollama_model",
    "resolve_ollama_think_option",
]

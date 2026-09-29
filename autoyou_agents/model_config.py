# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-1b797ff2c25fd4958d5add9d

"""
Model configuration module for AutoYou agents.

Provides dynamic model selection across multiple providers:
  - Ollama   (local, default preferred)
  - OpenClaw (local OpenAI-compatible gateway)
  - LiteLLM  (cloud: Anthropic, OpenAI, Mistral, DeepSeek, xAI, …)
  - Google   (Gemini via Google AI Studio or Vertex AI)
  - Apple Intelligence (on-device through the optional native macOS helper)

Provider is selected by the AI_PROVIDER environment variable, which is set
from config["ai_provider"]["provider"] at server startup and on save.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os
import logging
import re
from typing import Any, Dict, Optional
from google.adk.models.lite_llm import LiteLlm
from ollama_service import OllamaService
from shared.ollama_capabilities import (
    inspect_ollama_model_capabilities,
    resolve_installed_ollama_model,
    resolve_ollama_think_option,
)
from shared.ollama_context_policy import recommend_ollama_num_ctx

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-1b797ff2c25fd4958d5add9d"


logger = logging.getLogger(__name__)

BASE_OLLAMA_PROVIDER = "ollama_chat/"

# Provider constants
PROVIDER_OLLAMA   = "ollama"
PROVIDER_OPENCLAW = "openclaw"
PROVIDER_HERMES   = "hermes"
PROVIDER_LITELLM  = "litellm"
PROVIDER_GOOGLE   = "google"
PROVIDER_APPLE    = "apple_intelligence"

# Agent callbacks and prompts are intentionally model-specific. The compact
# profile is the safe default for unknown models and for Ministral 3B/8B.
# Gemma 4 uses the expanded harness across sizes because its observed failures
# are in the older compact path, while the expanded Gemma 4 path is the one
# covered by the recent LiteLLM/Ollama repair work.
AGENT_HARNESS_COMPACT = "compact"
AGENT_HARNESS_EXPANDED = "expanded"
_ACTIVE_OLLAMA_MODEL_ENV = "AUTOYOU_ACTIVE_OLLAMA_MODEL"
_ACTIVE_OLLAMA_PARAMETER_SIZE_ENV = "AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE"


def _model_identifier(model_name: Any = None) -> str:
    """Return a normalized model identifier from a name or LiteLlm object."""
    candidate = getattr(model_name, "model", None) if model_name is not None else None
    if not candidate:
        candidate = model_name
    if not candidate:
        candidate = os.getenv("OLLAMA_MODEL", "")
    identifier = str(candidate or "").strip().lower()
    return re.sub(r"^(?:ollama_chat|ollama|litellm)/", "", identifier)


def _model_size_billions(identifier: str) -> Optional[float]:
    match = re.search(r"(?:^|[:._-])([0-9]+(?:\.[0-9]+)?)b(?:$|[:._-])", identifier)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _parameter_size_billions(parameter_size: Any) -> Optional[float]:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*b\b", str(parameter_size or ""), re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _active_ollama_metadata_size_billions(identifier: str) -> Optional[float]:
    """Return the inspected Ollama parameter size for the active model tag."""
    active_model = _model_identifier(os.getenv(_ACTIVE_OLLAMA_MODEL_ENV, ""))
    if not active_model or active_model != str(identifier or "").strip().lower():
        return None
    return _parameter_size_billions(os.getenv(_ACTIVE_OLLAMA_PARAMETER_SIZE_ENV, ""))


def resolve_agent_harness_profile(model_name: Any = None) -> str:
    """Choose the agent harness for the selected model.

    ``AUTOYOU_AGENT_HARNESS_PROFILE`` accepts ``auto``, ``compact``, or
    ``expanded``. Automatic mode deliberately fails closed to compact for
    unknown models. Gemma 4 and known large local families opt into the
    expanded callbacks and prompts, while Ministral 3B/8B stay compact.
    Untagged or ``:latest`` models stay compact unless the active Ollama
    metadata reports a large parameter size for that exact selected tag.
    """
    override = str(os.getenv("AUTOYOU_AGENT_HARNESS_PROFILE", "") or "").strip().lower()
    if override in {"compact", "small", "minimal"}:
        return AGENT_HARNESS_COMPACT
    if override in {"expanded", "full", "large", "high"}:
        return AGENT_HARNESS_EXPANDED

    identifier = _model_identifier(model_name)
    if identifier.startswith("gpt-oss"):
        return AGENT_HARNESS_EXPANDED

    size = _model_size_billions(identifier)
    if size is None:
        size = _active_ollama_metadata_size_billions(identifier)
    if identifier.startswith("ministral-3"):
        return AGENT_HARNESS_EXPANDED if size is not None and size > 8 else AGENT_HARNESS_COMPACT

    if identifier.startswith("gemma4"):
        return AGENT_HARNESS_EXPANDED

    return AGENT_HARNESS_EXPANDED if size is not None and size >= 20 else AGENT_HARNESS_COMPACT


def model_uses_expanded_harness(model_name: Any = None) -> bool:
    """Return whether the selected model can use the expanded agent harness."""
    return resolve_agent_harness_profile(model_name) == AGENT_HARNESS_EXPANDED


def model_uses_compact_root_tool_routing(model_name: Any = None) -> bool:
    """Return whether the root should expose one specialist dispatcher.

    Callback complexity and root tool-schema capacity are separate concerns.
    Models such as ``gemma4:e2b`` and ``gemma4:e4b`` need expanded specialist callbacks, but
    is not reliable when the root advertises every installed specialist as a
    separate JSON schema. Keep its root on the compact two-stage topology so
    ordinary conversation remains a model-generated reply instead of needing a
    deterministic greeting shortcut.

    ``AUTOYOU_TWO_STAGE_ROUTER`` remains the explicit operator override at the
    root-agent layer. Apart from the e2b/e4b exception, this preserves the existing
    harness-profile routing behavior exactly.
    """
    identifier = _model_identifier(model_name)
    if re.match(r"^gemma4:e[24]b(?:$|[._-])", identifier):
        return True
    return not model_uses_expanded_harness(model_name)

# Kwargs that are Ollama-specific and must NOT be forwarded to cloud or
# OpenAI-compatible gateway endpoints that will reject unknown parameters.
_OLLAMA_ONLY_KWARGS = frozenset({"repeat_penalty", "num_ctx", "top_k", "think", "num_predict"})

# ── Mode presets (mirrors server.py _MODEL_BEHAVIOR_MODES) ───────────────────
_MODE_PRESETS_BASE: Dict[str, Dict[str, Any]] = {
    "none":     {},
    "accurate": {"temperature": 0.1, "top_p": 0.9, "repeat_penalty": 1.1},
    "human":    {"temperature": 0.7, "top_p": 0.95, "repeat_penalty": 1.05},
    "creative": {"temperature": 1.2, "top_p": 1.0,  "repeat_penalty": 0.9},
}

def _resolve_litellm_timeout_seconds() -> Optional[float]:
    """Return the configured LiteLLM timeout in seconds.

    Defaults to 1800s so long-running tool-heavy agent turns are not cut off
    by LiteLLM's shorter provider defaults. A non-positive value disables the
    explicit timeout kwarg.
    """
    raw_candidates = (
        os.getenv("AUTOYOU_LITELLM_TIMEOUT_SECONDS"),
        os.getenv("LITELLM_TIMEOUT_SECONDS"),
        os.getenv("LITELLM_TIMEOUT"),
        os.getenv("OLLAMA_TIMEOUT"),
        "1800",
    )
    for raw_value in raw_candidates:
        if raw_value is None:
            continue
        candidate = str(raw_value).strip()
        if not candidate:
            continue
        try:
            parsed = float(candidate)
        except Exception:
            continue
        if parsed <= 0:
            return None
        return parsed
    return 1800.0

def _resolve_ollama_thinking_enabled() -> bool:
    """Return whether Ollama models should expose provider thinking streams.

    Gemma 4 can emit a useful final answer in normal content when thinking is
    disabled, while ADK/LiteLLM may otherwise surface only a hidden thought part
    plus a malformed visible JSON stub. Keep thinking off for operator-facing
    AutoYou chats unless explicitly enabled for experimentation.

    The admin "Show model thinking" toggle (model_behavior.show_thinking) is
    authoritative once explicitly set (non-None) in config, same
    override-precedence pattern as resolve_ollama_num_ctx()'s num_ctx
    override; None (the untouched default) falls back to the env vars below.
    """
    cfg = _load_model_behavior_config()
    if isinstance(cfg, dict) and cfg.get("show_thinking") is not None:
        return bool(cfg.get("show_thinking"))

    raw_value = (
        os.getenv("AUTOYOU_OLLAMA_THINKING")
        or os.getenv("OLLAMA_THINKING")
        or os.getenv("OLLAMA_THINK")
        or ""
    )
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _resolve_ollama_thinking_level() -> Optional[str]:
    """Return an optional Ollama thinking level selected by the operator."""
    cfg = _load_model_behavior_config()
    raw_value = cfg.get("thinking_level") if isinstance(cfg, dict) else None
    if raw_value is None:
        raw_value = (
            os.getenv("AUTOYOU_OLLAMA_THINKING_LEVEL")
            or os.getenv("OLLAMA_THINKING_LEVEL")
            or ""
        )
    normalized = str(raw_value or "").strip().lower()
    return normalized if normalized in {"low", "medium", "high", "max"} else None

# A non-reasoning model spends its whole generation budget on the answer, so a
# small cap is a real bound on verbosity. A reasoning model spends the budget on
# its thinking trace *first* and only then writes the answer or the tool call -
# gpt-oss:120b ran out mid-JSON at 1024 and Ollama rejected its own half-written
# tool call with an HTTP 500, killing the run. The cap therefore has to know
# whether the turn includes reasoning.
_OLLAMA_NUM_PREDICT_DEFAULT = 1024
_OLLAMA_THINKING_NUM_PREDICT_DEFAULT = 4096

def _configured_ollama_num_predict() -> tuple[Optional[int], bool]:
    """Return (cap, operator_set). A non-positive configured value disables it."""
    raw_value = (
        os.getenv("AUTOYOU_OLLAMA_NUM_PREDICT")
        or os.getenv("OLLAMA_NUM_PREDICT")
        or ""
    )
    raw_value = str(raw_value).strip()
    if not raw_value:
        return None, False
    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning("Ignoring non-numeric Ollama num_predict override: %r", raw_value)
        return None, False
    return (parsed if parsed > 0 else None), True

def _resolve_ollama_num_predict(
    *,
    thinking: bool = False,
    num_ctx: Optional[int] = None,
) -> Optional[int]:
    """Return the Ollama generation-token cap.

    Ollama defaults can allow a local model to generate until the context is
    exhausted. Keep AutoYou turns bounded unless the operator explicitly
    disables the cap with a non-positive value.

    Reasoning models get a larger default because the trace is charged to this
    same budget. The result is still held under a quarter of the context window,
    since a cap the window cannot honour buys nothing.
    """
    configured, operator_set = _configured_ollama_num_predict()
    if operator_set:
        return configured

    default = _OLLAMA_THINKING_NUM_PREDICT_DEFAULT if thinking else _OLLAMA_NUM_PREDICT_DEFAULT
    window = _coerce_positive_int(num_ctx)
    if window:
        default = min(default, max(_OLLAMA_NUM_PREDICT_DEFAULT, window // 4))
    return default

def _think_option_enables_reasoning(think_option: Any) -> bool:
    """True when the resolved `think` value actually turns reasoning on.

    Ollama takes either a boolean or a level string here, and models without
    thinking support have the key removed entirely, so `False`, `None` and an
    absent key all mean the same thing: no reasoning trace to pay for.
    """
    if isinstance(think_option, bool):
        return think_option
    if isinstance(think_option, str):
        return bool(think_option.strip())
    return bool(think_option)

def _compose_litellm_kwargs(behavior: Dict[str, Any]) -> Dict[str, Any]:
    """Merge behavior params with runtime transport params for LiteLLM."""
    kwargs = dict(behavior or {})
    timeout_seconds = _resolve_litellm_timeout_seconds()
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    return kwargs

def _filter_ollama_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Remove Ollama-specific params that cloud / gateway endpoints reject."""
    return {k: v for k, v in kwargs.items() if k not in _OLLAMA_ONLY_KWARGS}

def _configured_ollama_model_name() -> str:
    return str(os.getenv("OLLAMA_MODEL", "ministral-3:8b") or "ministral-3:8b").strip()

def _load_model_behavior_config() -> Dict[str, Any]:
    try:
        # Lazy import to avoid circular dependency - server.py imports this module.
        import server as _srv

        cfg = (_srv.STATE.config or {}).get("model_behavior", {})
    except Exception:
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}

def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

def resolve_ollama_num_ctx(model_name: Optional[str] = None) -> tuple[int, str]:
    cfg = _load_model_behavior_config()
    explicit_override = _coerce_positive_int(cfg.get("num_ctx"))
    if explicit_override is not None:
        return explicit_override, "model_behavior.override"
    resolved_model_name = str(model_name or _configured_ollama_model_name() or "").strip()
    return recommend_ollama_num_ctx(resolved_model_name), "machine_heuristic"

def _build_mode_presets() -> Dict[str, Dict[str, Any]]:
    return {name: dict(params) for name, params in _MODE_PRESETS_BASE.items()}

def get_litellm_behavior_kwargs() -> Dict[str, Any]:
    """Read the current model_behavior config from ServerState and return LiteLlm kwargs.

    Priority (highest wins):
    1. Advanced per-field overrides stored in config["model_behavior"] (non-None values)
    2. Mode preset params  (e.g. accurate → temperature=0.1)
    3. Empty dict - no overrides applied, LiteLlm falls back to its own defaults
    """
    cfg = _load_model_behavior_config()
    mode = str(cfg.get("mode", "none")).lower()
    params = dict(_build_mode_presets().get(mode, {}))

    # Advanced per-field overrides
    for key, cast in [("temperature", float), ("top_p", float),
                      ("top_k", float), ("repeat_penalty", float), ("num_ctx", int)]:
        val = cfg.get(key)
        if val is not None:
            try:
                params[key] = cast(val)
            except (TypeError, ValueError):
                pass

    if params:
        logger.debug("model_behavior mode=%s resolved kwargs=%s", mode, params)
    return params

# ── Provider resolution ───────────────────────────────────────────────────────

def _get_active_provider() -> str:
    """Return the active provider string, with backward-compat fallback.

    Priority:
    1. AI_PROVIDER env var (set from config["ai_provider"]["provider"] at startup)
    2. Legacy USE_GOOGLE_API env var
    3. Default: PROVIDER_OLLAMA
    """
    p = os.getenv("AI_PROVIDER", "").strip().lower()
    if p in (PROVIDER_OLLAMA, PROVIDER_OPENCLAW, PROVIDER_HERMES, PROVIDER_LITELLM, PROVIDER_GOOGLE, PROVIDER_APPLE):
        return p
    # Backward compat: honour the old USE_GOOGLE_API flag
    if os.getenv("USE_GOOGLE_API", "0").lower() in ("1", "true", "yes"):
        return PROVIDER_GOOGLE
    return PROVIDER_OLLAMA

def get_model_config(ollama_service: OllamaService):
    """Determine the best available model configuration.

    Dispatches to the per-provider helper based on AI_PROVIDER env var.
    Ollama is the preferred local default. When its runtime is still starting,
    model validation is deferred so the complete agent graph can recover in
    place without silently switching providers.

    Args:
        ollama_service: Instance of OllamaService for checking Ollama availability.

    Returns:
        ADK model instance; LiteLlm for existing providers or the native Apple adapter.
    """
    provider = _get_active_provider()
    logger.info("Active AI provider: %s", provider)

    if provider == PROVIDER_APPLE:
        from autoyou_agents.apple_intelligence import AppleIntelligenceLlm
        return AppleIntelligenceLlm()
    if provider == PROVIDER_GOOGLE:
        return _configure_gemini_model()
    if provider == PROVIDER_OPENCLAW:
        return _configure_openclaw_model()
    if provider == PROVIDER_HERMES:
        return _configure_hermes_model()
    if provider == PROVIDER_LITELLM:
        return _configure_litellm_model()

    # Default: PROVIDER_OLLAMA
    # When Ollama is explicitly chosen, never silently fall back to the cloud.
    try:
        return _configure_ollama_model(ollama_service)
    except Exception as e:
        logger.warning("Ollama provider failed (%s). No automatic fallback when provider is 'ollama'.", e)
        raise

# ── Per-provider configurators ────────────────────────────────────────────────

def _configure_gemini_model() -> LiteLlm:
    """Configure and return Gemini (Google AI Studio or Vertex AI) model."""
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    use_vertex_ai = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1", "yes")
    base_model_name = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")

    if use_vertex_ai:
        logger.info("GOOGLE_GENAI_USE_VERTEXAI is enabled, using Vertex AI")
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            raise ValueError(
                "Vertex AI configuration incomplete. Set GOOGLE_CLOUD_PROJECT "
                "and authenticate with 'gcloud auth login', or disable Vertex AI."
            )
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        model_name = f"vertex_ai/{base_model_name}"
        logger.info("Using Vertex AI project=%s location=%s model=%s", project_id, location, model_name)
    else:
        if not api_key or api_key == "NULL":
            raise ValueError(
                "No Google API key found. Set GOOGLE_API_KEY or configure it in "
                "Admin Dashboard → AI Agent Server Control → Google Gemini."
            )
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
        model_name = f"gemini/{base_model_name}"
        logger.info("Using Google AI Studio model=%s", model_name)

    behavior = get_litellm_behavior_kwargs()
    kwargs = _filter_ollama_kwargs(_compose_litellm_kwargs(behavior))
    if kwargs:
        logger.info("LiteLlm Gemini kwargs: %s", kwargs)
        return LiteLlm(model=model_name, **kwargs)
    return LiteLlm(model=model_name)

def _configure_openclaw_model() -> LiteLlm:
    """Route completions through OpenClaw's local OpenAI-compatible HTTP API.

    OpenClaw must be running locally. AutoYou sends chat completions to
    OpenClaw's gateway; OpenClaw routes to whichever LLM it has configured
    (Ollama, Anthropic, etc.). AutoYou's ADK sub-agents and tools remain
    fully functional - OpenClaw is a transparent LLM backend.

    Default port: 18789 (OpenClaw HTTP gateway).
    Model aliases: openclaw/default (fast), nadirclaw/eco (capable),
                   openclaw:<agentId> (invoke a full OpenClaw agent).
    """
    port        = int(os.getenv("OPENCLAW_PORT", "18789"))
    token       = os.getenv("OPENCLAW_TOKEN", "") or "noop"
    model_alias = os.getenv("OPENCLAW_MODEL", "openclaw/default") or "openclaw/default"

    behavior = get_litellm_behavior_kwargs()
    # Strip Ollama-specific kwargs - OpenClaw's HTTP API is OpenAI-compatible
    # and will reject unknown parameters.
    kwargs = _filter_ollama_kwargs(_compose_litellm_kwargs(behavior))

    logger.info("Using OpenClaw provider port=%d model=%s", port, model_alias)
    return LiteLlm(
        model    = f"openai/{model_alias}",   # openai/ prefix = any OpenAI-compat endpoint
        api_base = f"http://127.0.0.1:{port}/v1",
        api_key  = token,
        **kwargs,
    )

def _configure_hermes_model() -> LiteLlm:
    """Route completions through the local NousResearch Hermes Agent gateway.

    Hermes Agent must be running locally (`hermes gateway`).  AutoYou sends
    chat completions to the Hermes HTTP gateway, which runs the full Hermes
    agent loop (memory, tools, MCP integrations) before returning a response.
    AutoYou's own ADK sub-agents and tools remain fully functional.

    Default port: 8642 (Hermes HTTP gateway, set via API_SERVER_PORT env var).
    Default model: hermes-agent (set via API_SERVER_MODEL_NAME env var in Hermes).
    Auth: optional Bearer token (set via API_SERVER_KEY env var in Hermes).
    """
    port        = int(os.getenv("HERMES_PORT", "8642"))
    token       = os.getenv("HERMES_TOKEN", "") or "noop"
    model_alias = os.getenv("HERMES_MODEL", "hermes-agent") or "hermes-agent"

    behavior = get_litellm_behavior_kwargs()
    # Strip Ollama-specific kwargs - Hermes HTTP API is OpenAI-compatible
    kwargs = _filter_ollama_kwargs(_compose_litellm_kwargs(behavior))

    logger.info("Using Hermes Agent provider port=%d model=%s", port, model_alias)
    return LiteLlm(
        model    = f"openai/{model_alias}",   # openai/ prefix = any OpenAI-compat endpoint
        api_base = f"http://127.0.0.1:{port}/v1",
        api_key  = token,
        **kwargs,
    )

def _configure_litellm_model() -> LiteLlm:
    """Configure LiteLLM for cloud API providers.

    Supports any provider string LiteLLM understands, e.g.:
      anthropic/claude-opus-4-5
      anthropic/claude-sonnet-4-5
      anthropic/claude-haiku-4-5
      openai/gpt-4o
      openai/gpt-4o-mini
      mistral/mistral-large-latest
      deepseek/deepseek-chat
      xai/grok-3

    The model string, API key, and optional custom base URL are all read
    from environment variables set from config["ai_provider"].
    """
    model    = os.getenv("LITELLM_MODEL",    "").strip()
    api_key  = os.getenv("LITELLM_API_KEY",  "").strip() or None
    # from __debug_provenance_v__ import wallet
    api_base = os.getenv("LITELLM_API_BASE", "").strip() or None

    if not model:
        raise ValueError(
            "LiteLLM provider selected but LITELLM_MODEL is not configured. "
            "Set it in Admin Dashboard → AI Agent Server Control → LiteLLM Cloud."
        )

    behavior = get_litellm_behavior_kwargs()
    kwargs   = _filter_ollama_kwargs(_compose_litellm_kwargs(behavior))
    if api_base:
        kwargs["api_base"] = api_base

    logger.info("Using LiteLLM cloud provider model=%s base=%s", model, api_base or "(default)")
    return LiteLlm(model=model, api_key=api_key, **kwargs)

def _configure_ollama_model(ollama_service: OllamaService) -> LiteLlm:
    """Configure and return Ollama model configuration."""
    model_name = _configured_ollama_model_name()
    logger.info("Attempting to use Ollama model: %s", model_name)

    if ollama_service.is_available():
        available_models = ollama_service.list_models()
        if not available_models:
            raise RuntimeError("Ollama is running but has no models installed")

        selected_model = _find_available_model(model_name, available_models)
        if not selected_model:
            explicit_selection = str(os.getenv("AUTOYOU_OLLAMA_MODEL_EXPLICIT", "0") or "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if explicit_selection:
                available_names = ", ".join(str(name) for name in available_models) or "none"
                raise RuntimeError(
                    f"Configured Ollama model '{model_name}' is not installed. "
                    f"Available models: {available_names}"
                )
            selected_model = _select_fallback_model(available_models, ollama_service)
            logger.warning(
                "Configured default Ollama model '%s' is not installed; using fallback '%s'. "
                "Choose an installed model in the model library to make the selection explicit.",
                model_name,
                selected_model,
            )
        if not selected_model:
            raise RuntimeError("No suitable Ollama model found in available models")
    else:
        # Keep the complete agent/tool graph alive while the local runtime is
        # starting. LiteLLM connects on the first turn, so model validation can
        # safely wait; rebuilding a minimal root here permanently removed every
        # specialist until AutoYou itself was restarted.
        selected_model = model_name
        logger.warning(
            "Ollama is not reachable yet; deferring validation of model '%s' so the full agent graph can recover in place",
            selected_model,
        )

    logger.info("Using Ollama model: %s", selected_model)

    api_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
    os.environ["OLLAMA_API_BASE"] = api_base
    os.environ["OLLAMA_MODEL"] = selected_model
    os.environ[_ACTIVE_OLLAMA_MODEL_ENV] = selected_model
    os.environ.pop(_ACTIVE_OLLAMA_PARAMETER_SIZE_ENV, None)

    behavior = get_litellm_behavior_kwargs()
    merged = _compose_litellm_kwargs(behavior)
    # Default temperature for local Ollama tool-calling agents to 0.1 when
    # unconfigured, preventing llama.cpp default (temp=1.0) from causing control-token
    # loops or generation collapse under large tool-schema prompts.
    #
    # A low temperature alone trades one collapse for another: near-greedy
    # sampling with no repetition control makes a small model fall into a
    # degenerate loop, observed with ministral-3:8b emitting "retrie: retrie:"
    # for a full 1024-token budget. The default mode is "none", which supplies
    # no sampling parameters at all, so the temperature floor has to carry its
    # own repetition guard. These mirror the "accurate" preset and are only
    # defaults - a configured mode or per-field override still wins.
    merged.setdefault("temperature", 0.1)
    merged.setdefault("top_p", 0.9)
    merged.setdefault("repeat_penalty", 1.1)
    if _coerce_positive_int(merged.get("num_ctx")) is None:
        merged["num_ctx"], num_ctx_source = resolve_ollama_num_ctx(selected_model)
    else:
        num_ctx_source = "model_behavior.override"
    thinking_enabled = _resolve_ollama_thinking_enabled()
    thinking_level = _resolve_ollama_thinking_level()
    capability = None
    service_client = getattr(ollama_service, "client", None)
    if service_client is not None:
        capability = inspect_ollama_model_capabilities(
            api_base,
            selected_model,
            client=service_client,
        )
    if capability and capability.get("available"):
        parameter_size = str(capability.get("parameter_size") or "").strip()
        if parameter_size:
            os.environ[_ACTIVE_OLLAMA_PARAMETER_SIZE_ENV] = parameter_size
        think_option = resolve_ollama_think_option(
            selected_model,
            thinking_enabled,
            level=thinking_level,
            capabilities=capability,
        )
        if think_option is None:
            merged.pop("think", None)
        else:
            merged["think"] = think_option
        logger.info(
            "Ollama model capabilities: model=%s family=%s capabilities=%s thinking=%s option=%s",
            selected_model,
            capability.get("family", ""),
            capability.get("capabilities", []),
            capability.get("supports_thinking"),
            think_option,
        )
    else:
        # Older Ollama servers may not expose /api/show capabilities. Preserve
        # the legacy boolean in that unknown-capability case, but never use it
        # when Ollama explicitly reports that a model lacks thinking support.
        merged.setdefault("think", thinking_enabled)
        logger.info("Ollama thinking capability unavailable; using compatibility option=%s", merged.get("think"))
    reasoning_enabled = _think_option_enables_reasoning(merged.get("think"))
    num_predict = _resolve_ollama_num_predict(
        thinking=reasoning_enabled,
        num_ctx=merged.get("num_ctx"),
    )
    if num_predict is not None:
        merged.setdefault("num_predict", num_predict)
    logger.info("Resolved Ollama num_ctx=%s source=%s", merged.get("num_ctx"), num_ctx_source)
    logger.info(
        "Resolved Ollama num_predict=%s (reasoning=%s think=%r)",
        merged.get("num_predict"),
        reasoning_enabled,
        merged.get("think"),
    )
    logger.info("LiteLlm Ollama kwargs: %s", merged)

    return LiteLlm(
        model    = BASE_OLLAMA_PROVIDER + selected_model,
        api_base = api_base,
        **merged,
    )

# ── Model selection helpers ───────────────────────────────────────────────────

def _find_available_model(model_name: str, model_names: list) -> Optional[str]:
    """Find the best matching model from available Ollama models."""
    try:
        return resolve_installed_ollama_model(model_name, model_names)
    except ValueError:
        return None

def _select_fallback_model(model_names: list, ollama_service: OllamaService) -> Optional[str]:
    """Select a fallback model from available Ollama models."""
    return ollama_service.get_latest_model() or ollama_service.get_default_model()

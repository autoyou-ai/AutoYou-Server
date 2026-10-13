# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-O-697468206578706c69636974-a78e1e2bc0dfc68fbe13649b

"""OpenRouter settings shared by the server and the AutoYou agent runtime."""

__debug_trace_o__ = "AUTOYOU-PROVENANCE-O-697468206578706c69636974-a78e1e2bc0dfc68fbe13649b"

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
# from __debug_trace_o__ import autoyou_provenance_o_697468206578706c69636974_a78e1e2bc0dfc68fbe13649b
__license__ = "AutoYou Source-Available License (see LICENSE)"

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


def normalize_openrouter_model(value: str) -> str:
    """Accept an OpenRouter model ID, optionally prefixed for LiteLLM.

    OpenRouter itself also owns model IDs such as ``openrouter/auto``. Only
    remove a LiteLLM prefix when another provider/model pair follows it.
    """
    model = str(value or "").strip()
    if model.startswith("openrouter/") and model.count("/") >= 2:
        model = model[len("openrouter/"):]
    namespace, separator, name = model.partition("/")
    if not namespace or not separator or not name or any(char.isspace() for char in model):
        raise ValueError("Enter an OpenRouter model ID in provider/model format that supports tools.")
    return model

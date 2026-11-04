# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-58af5926885d9a0624965f8b

"""Local-first agent harness helpers.

These helpers keep provider-specific orchestration quirks out of individual
agents.  They are intentionally dependency-light so API and test modules can
use them without importing the full ADK agent graph.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import re
from typing import Any

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-58af5926885d9a0624965f8b"


_FINAL_RESPONSE_HINTS = (
    "added",
    "admin session is active",
    "already",
    "completed",
    "confirmed",
    "created",
    "done",
    "error",
    "failed",
    "found",
    "generated",
    "queued",
    "playing",
    "saved",
    "scheduled",
    "sent",
    "success",
    "there is no",
    "updated",
    # Spanish.
    "listo",
    "hecho",
    "guardado",
    "guardada",
    "creado",
    "creada",
    "enviado",
    "enviada",
    "programado",
    "programada",
    "completado",
    "completada",
    "añadido",
    "añadida",
    "actualizado",
    "actualizada",
    "encontré",
    "encontrado",
    "encontrada",
    "falló",
    "no hay",
    "ya está",
)

# English and Spanish (owner, 2026-11-03: English + Spanish for now; wider
# language coverage belongs to a structural check, not more word lists).
# Look-up steps only. Words that also confirm work already under way ("I'm
# working on your image", "preparing your report") are left out on purpose:
# this check also runs on specialist results, and marking a started job as
# unfinished could make the root start it again.
_STEP_VERBS = (
    r"(?:check|search|look|scan|open|inspect|read|analy[sz]e|investigate|review|find|calculate|compute)"
)
_STEP_GERUNDS = (
    r"(?:going|checking|searching|looking|reading|scanning|opening|inspecting|analy[sz]ing|investigating|"
    r"reviewing|calculating|computing)"
)
_STEP_VERBS_ES = (
    r"(?:buscar|revisar|comprobar|verificar|consultar|mirar|abrir|leer|analizar|investigar|calcular|encontrar)"
)
_STEP_GERUNDS_ES = (
    r"(?:buscando|revisando|comprobando|verificando|consultando|mirando|abriendo|leyendo|analizando|"
    r"investigando|calculando)"
)
_PROGRESS_ONLY_PATTERNS = (
    re.compile(r"^\s*(?:let me|lemme)\b", re.IGNORECASE),
    re.compile(rf"^\s*i(?:'| a)?m\s+(?:now\s+)?{_STEP_GERUNDS}\b", re.IGNORECASE),
    re.compile(rf"^\s*i(?:'ll|\s+will|ll)\s+(?:now\s+|first\s+|quickly\s+)?{_STEP_VERBS}\b", re.IGNORECASE),
    re.compile(rf"^\s*i\s+need\s+to\s+{_STEP_VERBS}\b", re.IGNORECASE),
    re.compile(rf"^\s*{_STEP_GERUNDS}\b", re.IGNORECASE),
    re.compile(r"^\s*(?:one moment|hold on|stand by|working on it)\b", re.IGNORECASE),
    # Spanish: "Déjame...", "Voy a revisar...", "Estoy buscando...".
    re.compile(r"^\s*(?:d[ée]jame|perm[íi]teme)\b", re.IGNORECASE),
    re.compile(rf"^\s*(?:voy|vamos)\s+a\s+(?:ahora\s+)?{_STEP_VERBS_ES}\b", re.IGNORECASE),
    re.compile(rf"^\s*(?:estoy|estamos)\s+(?:ahora\s+)?{_STEP_GERUNDS_ES}\b", re.IGNORECASE),
    re.compile(rf"^\s*{_STEP_GERUNDS_ES}\b", re.IGNORECASE),
    re.compile(r"^\s*(?:un momento|un segundo|un instante)\b", re.IGNORECASE),
)
# Lines that may follow a progress line without delivering anything.
_FILLER_PATTERNS = (
    re.compile(
        r"^\s*(?:one moment|just a (?:moment|sec(?:ond)?)|hold on|hang on|please (?:wait|hold on|bear with me)|"
        r"bear with me|stand by|working on it|this (?:may|might|will|could) take a (?:moment|minute|few (?:seconds|minutes)))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:un momento|un segundo|un instante|espera(?:\s+un\s+momento)?|dame un (?:momento|segundo)|"
        r"enseguida|esto (?:puede|podr[íi]a) tardar(?: un (?:momento|poco)| unos (?:segundos|minutos))?)\b",
        re.IGNORECASE,
    ),
)
# "Let me know if..." closes an answer; it does not announce a step.
_CLOSING_LINE = re.compile(r"^\s*(?:let me know|av[íi]same|d[íi]me si|h[áa]zmelo saber)\b", re.IGNORECASE)
_SEGMENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def normalize_agent_text(value: Any) -> str:
    """Return a compact text representation suitable for harness decisions."""
    return " ".join(str(value or "").strip().split())


def _matches_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def is_progress_only_response(value: Any) -> bool:
    """Return True when a reply only announces a next step and delivers nothing.

    Local OpenAI-compatible models are prone to ending a turn or a tool call
    with planning text such as "Let me search..." instead of a result. Such a
    reply is all announcement: every line or sentence is a step ("Let me check
    that.") or filler ("One moment."). Anything delivered wins over the
    opening words - a result ("= 1923"), content after a colon, a list or
    another sentence - because "Let me calculate that: 37 x 52 = 1924 ..." is an
    answer, and treating it as progress threw the answer away (owner report
    2026-11-03). Completion/status words also win, so confirmations still flow
    through immediately.
    """
    raw = str(value or "").strip()
    text = normalize_agent_text(raw)
    if not text:
        return False
    lowered = text.lower()
    if any(hint in lowered for hint in _FINAL_RESPONSE_HINTS):
        return False
    # Spanish opens questions and exclamations with ¿ and ¡.
    if len(text) > 240 or _CLOSING_LINE.match(text.lstrip("¡¿")) or "=" in text:
        return False
    segments = [segment.strip().lstrip("¡¿").strip() for segment in _SEGMENT_SPLIT.split(raw) if segment.strip()]
    if not segments or not _matches_any(_PROGRESS_ONLY_PATTERNS, segments[0]):
        return False
    first = segments[0]
    colon = first.find(":")
    if colon != -1 and len(first[colon + 1:].strip()) >= 12:
        return False
    return all(
        _matches_any(_PROGRESS_ONLY_PATTERNS, segment) or _matches_any(_FILLER_PATTERNS, segment)
        for segment in segments[1:]
    )


def nonfinal_tool_response(tool_name: str, progress_text: str) -> dict[str, Any]:
    """Build a replacement tool response that tells the root not to finalize."""
    normalized_tool_name = str(tool_name or "").strip() or "specialist"
    normalized_text = normalize_agent_text(progress_text)
    # from __debug_provenance_s__ import btc
    return {
        "status": "incomplete",
        "progress_only": True,
        "message": (
            "The specialist tool returned only a progress update, not a completed "
            "answer. Do not send that progress line to the user as the final "
            "response. Continue the specialist task if possible; otherwise state "
            "that the specialist did not complete the request."
        ),
        "tool_name": normalized_tool_name,
        "progress_text": normalized_text,
    }

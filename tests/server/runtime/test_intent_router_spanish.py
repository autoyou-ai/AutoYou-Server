# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The learned router handles Spanish as well as English (owner, 2026-11-03).

Spanish examples sit beside the English ones in routes.json. With the current
English encoder they route close Spanish requests and leave the rest to the
language model; they never send an English request elsewhere.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from pathlib import Path

import pytest

from shared import intent_router

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def router():
    pytest.importorskip("onnxruntime")
    return intent_router.IntentRouter(ROOT / "assets/intent_router")


@pytest.mark.parametrize("text,route", [
    ("pon un poco de jazz de mi música", "audio_agent"),
    ("publica mi foto de vacaciones en mi página", "page_agent"),
    ("programa un resumen de noticias cada mañana", "tasks_agent"),
])
def test_close_spanish_requests_are_routed(router, text, route):
    assert router.classify(text)["route"] == route


@pytest.mark.parametrize("text", [
    "¿qué tiempo hará mañana en Madrid?",
    "guarda una nota de que cambió la contraseña del wifi",
    "cuéntame algo gracioso hoy",
])
def test_uncertain_spanish_requests_go_to_the_language_model(router, text):
    assert router.classify(text)["route"] is None


@pytest.mark.parametrize("text", [
    "no pongas música ahora",
    "en vez de música, pon las noticias",
    "nunca publiques mis fotos",
])
def test_spanish_negations_are_left_to_the_language_model(router, text):
    assert router.classify(text) == {"route": None, "reason": "needs_context"}


def test_english_routing_is_unchanged(router):
    assert router.classify("what is today's date please")["route"] == "datetime"
    assert router.classify("tell me something funny today")["route"] is None

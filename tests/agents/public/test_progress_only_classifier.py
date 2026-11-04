# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""A reply is "progress only" when it announces a step and delivers nothing.

Owner report 2026-11-03: "What is 37x52-3/3" was answered with "Let me calculate
that:" followed by the working and a result, and the server threw that answer
away as a progress update because of its first words. Announcements are still
caught, so the turn can continue instead of ending on them.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import pytest

from autoyou_agents.agent_harness import is_progress_only_response

THE_OWNERS_ANSWER = (
    "Let me calculate that:\n\n37 × 52 = 1,924\n1,924 - 3 = 1,921\n1,921 ÷ 3 = **640.33...** (or 640⅓)"
)


@pytest.mark.parametrize("reply", [
    "Let me search for that.",
    "Let me check the latest news for you.",
    "Let me calculate that.",
    "Let me calculate 37x52-3/3.",
    "Let me check that for you:",
    "Let me search the web for that. One moment.",
    "Let me search for that... Please wait.",
    "I'll look that up for you.",
    "I will check the files now.",
    "I'm checking the calendar.",
    "Checking now...",
    "Searching for flights from Paris to Rome.",
    "Let me directly search for the class initialization",
    "Let me open the page. I'll read it next.",
    # Spanish (owner, 2026-11-03: English + Spanish for now).
    "Déjame buscarlo.",
    "Permíteme comprobarlo.",
    "Voy a revisar tus notas.",
    "Estoy buscando eso. Un momento.",
    "Buscando vuelos a Roma...",
    "¡Déjame calcularlo!",
])
def test_announcements_are_progress_only(reply):
    assert is_progress_only_response(reply) is True


@pytest.mark.parametrize("reply", [
    THE_OWNERS_ANSWER,
    "Let me explain: photosynthesis turns light into chemical energy.",
    "Let me be clear: the answer is 42.",
    "Let me check. The weather in Paris is 18 degrees and sunny.",
    "Let me summarize:\n- the meeting moved to Friday\n- Sam is presenting",
    "Let me know if you need anything else.",
    "Let me check: 2+2=4.",
    "I'll check... done, the backup finished.",
    "The answer is 1923.",
    "",
    "Déjame calcularlo: 37 × 52 = 1924.",
    "Déjame revisar. El vuelo sale a las ocho.",
    "Avísame si necesitas algo más.",
    "Listo, guardé la nota.",
    "Voy a revisar tus notas: tienes tres sobre el proyecto.",
])
def test_anything_delivered_is_an_answer(reply):
    assert is_progress_only_response(reply) is False


@pytest.mark.parametrize("reply", [
    "I'm working on your image; I'll send it here when it is ready.",
    "Image generation has started. I'll send it here when it is ready.",
    "I'm preparing your report now.",
    "Sent to Codex. Its final answer will be delivered back to this conversation.",
])
def test_work_already_under_way_is_not_an_unfinished_step(reply):
    """This check also runs on specialist results: marking a started job as
    unfinished could make the root start it again."""
    assert is_progress_only_response(reply) is False

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-f709d1fc2db6d487ebe53ce2

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Regenerate ``tests/fixtures/peer_assistant/v1.json`` from the reference.

The Peer Assistant rules exist in Python (``clients/python/peer_link/assistant.py``),
Kotlin and Swift. Every expected value in the corpus is computed by the Python
reference, so a vector can never bless a bug the reference does not have; the
Kotlin and Swift suites then assert they produce the same rows.

    python scripts/generate_peer_assistant_corpus.py          # rewrite
    python scripts/generate_peer_assistant_corpus.py --check  # fail on drift
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import json
import sys
from pathlib import Path

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-f709d1fc2db6d487ebe53ce2"


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "clients" / "python"))

from peer_link import assistant as pa  # noqa: E402

CORPUS = REPO_ROOT / "tests" / "fixtures" / "peer_assistant" / "v1.json"


def _decision_case(name, **kwargs):
    defaults = dict(
        mode=pa.MODE_AUTO,
        kind=pa.KIND_PEER,
        link_direct=True,
        inbound_auto_reply=False,
        text="Are you free later?",
        recent_reply_times=[],
        now=1000.0,
        hybrid_delay_seconds=pa.DEFAULT_HYBRID_DELAY_SECONDS,
    )
    defaults.update(kwargs)
    decision = pa.decide(**defaults)
    return {
        "name": name,
        **defaults,
        "expected": {
            "action": decision.action,
            "reason": decision.reason,
            "delay_seconds": decision.delay_seconds,
        },
    }


def _turns(*pairs):
    return [{"role": role, "text": text} for role, text in pairs]


def _prompt_case(name, config, device_name, peer_name, history, incoming_text):
    built = pa.build_prompt(
        config=pa.PeerAssistantConfig.from_dict(config),
        device_name=device_name,
        peer_name=peer_name,
        history=[pa.PeerAssistantTurn(role=t["role"], text=t["text"]) for t in history],
        incoming_text=incoming_text,
    )
    return {
        "name": name,
        "config": config,
        "device_name": device_name,
        "peer_name": peer_name,
        "history": history,
        "incoming_text": incoming_text,
        "expected": {"instructions": built.instructions, "prompt": built.prompt},
    }


def build() -> dict:
    long_history = _turns(
        *[
            (pa.ROLE_PEER if index % 2 == 0 else pa.ROLE_ASSISTANT, f"Message number {index} " + "x" * 120)
            for index in range(20)
        ]
    )
    # from __debug_provenance_c__ import subtask
    split_cases = []
    for name, history, fallback in (
        ("no_history", [], "hello"),
        ("single_unanswered", _turns(("assistant", "Earlier reply"), ("peer", "One")), "One"),
        (
            "burst_of_three",
            _turns(("peer", "Old"), ("owner", "Owner answer"), ("peer", "A"), ("peer", "B"), ("peer", "C")),
            "C",
        ),
        ("ends_with_owner", _turns(("peer", "Q"), ("owner", "Answered")), "fresh"),
    ):
        remaining, text = pa.split_unanswered(
            [pa.PeerAssistantTurn(role=t["role"], text=t["text"]) for t in history], fallback
        )
        split_cases.append(
            {
                "name": name,
                "history": history,
                "fallback_text": fallback,
                "expected": {"history": [t.to_dict()["role"] + ":" + t.text for t in remaining], "text": text},
            }
        )

    sanitize_inputs = [
        ("plain", "Sure, I'll let them know."),
        ("surrounding_whitespace", "  \n Hello there!  \n"),
        ("think_block", "<think>They want a time.</think>\nI'll ask the owner and get back to you."),
        ("think_block_uppercase", "<THINK>\nplan\n</THINK>Happy to help."),
        ("unclosed_think", "Okay. <think>still reasoning about"),
        ("only_think", "<think>nothing else</think>"),
        ("speaker_label", "Assistant: The owner is away until Friday."),
        ("speaker_label_you", "you:   Noted!"),
        ("wrapped_quotes", '"I will pass that on."'),
        ("curly_quotes", "“Thanks for the message!”"),
        ("inner_quotes_kept", '"Yes" or "No"'),
        ("blank_lines", "Line one\n\n\n\nLine two   \nLine three"),
        ("long_sentences", ("This sentence is here to fill space. " * 60).strip()),
        ("long_unbroken", "y" * 1500),
        ("empty", ""),
    ]
    display_cases = [
        ("", "Pixel 9"),
        ("", ""),
        ("Sam's assistant", "Pixel 9"),
        ("Concierge AI", "iPhone"),
        ("HelperBot", "iPhone"),
        ("Help bot", "iPhone"),
        ("Aswath", "Pixel 9"),
        ("  Maya  ", "Galaxy"),
        ("", "  Desk PC  "),
        ("A" * 60, "Phone"),
    ]
    base_config = {"mode": "auto_bot"}
    custom_config = {
        "mode": "hybrid",
        "instructions": "Answer questions about the book club. Keep it friendly.",
        "assistant_name": "Club Assistant",
        "knowledge": "Next meeting: Thursday 7pm at the library.\nBook: Dune.",
    }
    return {
        "format": "autoyou.peer-assistant/1",
        "purpose": (
            "Cross-implementation vectors for the Peer Assistant (auto-reply harness for a device "
            "hosting a DIRECT peer link). clients/python/peer_link/assistant.py is the reference; "
            "PeerAssistant.kt and PeerAssistant.swift must reproduce every row. Generated by "
            "scripts/generate_peer_assistant_corpus.py - never edit by hand."
        ),
        "constants": {
            "modes": list(pa.MODES),
            "engines": list(pa.ENGINES),
            "default_instructions": pa.DEFAULT_INSTRUCTIONS,
            "instruction_templates": [
                {"id": ident, "title": title, "instructions": text} for ident, title, text in pa.INSTRUCTION_TEMPLATES
            ],
            "default_hybrid_delay_seconds": pa.DEFAULT_HYBRID_DELAY_SECONDS,
            "min_hybrid_delay_seconds": pa.MIN_HYBRID_DELAY_SECONDS,
            "max_hybrid_delay_seconds": pa.MAX_HYBRID_DELAY_SECONDS,
            "max_instructions_chars": pa.MAX_INSTRUCTIONS_CHARS,
            "max_knowledge_chars": pa.MAX_KNOWLEDGE_CHARS,
            "max_assistant_name_chars": pa.MAX_ASSISTANT_NAME_CHARS,
            "max_reply_chars": pa.MAX_REPLY_CHARS,
            "memory_turns_per_peer": pa.MEMORY_TURNS_PER_PEER,
            "memory_max_peers": pa.MEMORY_MAX_PEERS,
            "rate_window_seconds": pa.RATE_WINDOW_SECONDS,
            "rate_max_replies": pa.RATE_MAX_REPLIES,
            "basic_reply_cooldown_seconds": pa.BASIC_REPLY_COOLDOWN_SECONDS,
            "metadata_auto_reply": pa.METADATA_AUTO_REPLY,
            "metadata_auto_reply_engine": pa.METADATA_AUTO_REPLY_ENGINE,
            "metadata_agent_display_name": pa.METADATA_AGENT_DISPLAY_NAME,
        },
        "modes": [
            {"input": value, "expected": pa.normalize_mode(value)}
            for value in ["manual", "auto_bot", "hybrid", " AUTO_BOT ", "Hybrid", "", "auto", "bot", "unknown"]
        ],
        "engines": [
            {"input": value, "expected": pa.normalize_engine(value)}
            for value in ["auto", "on_device", "local_server", "basic", " BASIC", "", "gemini", "ollama"]
        ],
        "hybrid_delays": [
            {"input": value, "expected": pa.clamp_hybrid_delay(value)}
            for value in [45, 10, 9, 0, -5, 600, 601, 90]
        ],
        "endpoints": [
            {"input": value, "expected": pa.normalize_endpoint(value)}
            for value in [
                "http://192.168.1.20:11434",
                "http://192.168.1.20:11434/",
                "HTTPS://models.local/v1/",
                "  http://127.0.0.1:1234/v1  ",
                "ftp://example.com",
                "192.168.1.20:11434",
                "http://",
                "",
            ]
        ],
        "display_names": [
            {"assistant_name": name, "device_name": device, "expected": pa.assistant_display_name(name, device)}
            for name, device in display_cases
        ],
        "engine_order": [
            {"choice": choice, "on_device_supported": supported, "expected": pa.engine_order(choice, on_device_supported=supported)}
            for choice in pa.ENGINES
            for supported in (True, False)
        ],
        "decisions": [
            _decision_case("auto_replies_now"),
            _decision_case("hybrid_waits_default", mode=pa.MODE_HYBRID),
            _decision_case("hybrid_waits_custom", mode=pa.MODE_HYBRID, hybrid_delay_seconds=120),
            _decision_case("hybrid_delay_is_clamped", mode=pa.MODE_HYBRID, hybrid_delay_seconds=3),
            _decision_case("manual_never_replies", mode=pa.MODE_MANUAL),
            _decision_case("unknown_mode_is_manual", mode="sometimes"),
            _decision_case("computer_bridge_is_not_peer_chat", kind=pa.KIND_COMPUTER),
            _decision_case("relayed_traffic_is_not_answered", kind=pa.KIND_RELAYED),
            _decision_case("relay_mode_server_answers", link_direct=False),
            _decision_case("never_answer_another_assistant", inbound_auto_reply=True),
            _decision_case("manual_wins_over_loop_guard", mode=pa.MODE_MANUAL, inbound_auto_reply=True),
            _decision_case("blank_message", text="   \n  "),
            _decision_case(
                "rate_limit_reached",
                recent_reply_times=[950.0, 960.0, 970.0, 980.0, 990.0],
            ),
            _decision_case(
                "rate_limit_window_expired",
                recent_reply_times=[900.0, 910.0, 920.0, 930.0, 940.0],
            ),
            _decision_case(
                "rate_limit_partially_expired",
                recent_reply_times=[939.0, 941.0, 960.0, 970.0, 980.0],
            ),
        ],
        "split_unanswered": split_cases,
        "sanitize": [
            {"name": name, "input": value, "expected": pa.sanitize_reply(value)} for name, value in sanitize_inputs
        ],
        "prompts": [
            _prompt_case("defaults_no_history", base_config, "Pixel 9", "Desk PC", [], "Hi! Is Sam around?"),
            _prompt_case(
                "custom_with_history",
                custom_config,
                "Sam's iPhone",
                "Maya",
                _turns(
                    ("peer", "Hey, when is the next meeting?"),
                    ("assistant", "Thursday at 7pm at the library."),
                    ("owner", "See you there!"),
                ),
                "Which book are we reading?",
            ),
            _prompt_case(
                "whitespace_is_flattened",
                {"mode": "auto_bot", "knowledge": "  "},
                "  Galaxy   S25 ",
                " Lee\n Park ",
                _turns(("peer", "line one\nline two\t tabbed")),
                "  spaced   out  ",
            ),
            _prompt_case("long_history_is_trimmed_to_budget", base_config, "Pixel 9", "Desk PC", long_history, "Still there?"),
            _prompt_case(
                "long_turns_and_message_are_clipped",
                base_config,
                "Pixel 9",
                "Desk PC",
                _turns(("peer", "z" * 700)),
                "w" * 1200,
            ),
            _prompt_case("blank_names_fall_back", base_config, "", "", [], "Hello?"),
        ],
        "basic_replies": [
            {
                "config": config,
                "device_name": device,
                "peer_name": peer,
                "expected": pa.basic_reply(
                    config=pa.PeerAssistantConfig.from_dict(config), device_name=device, peer_name=peer
                ),
            }
            for config, device, peer in (
                (base_config, "Pixel 9", "Desk PC"),
                (custom_config, "Sam's iPhone", "Maya"),
                (base_config, "", ""),
            )
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the corpus is out of date")
    args = parser.parse_args()
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        current = CORPUS.read_text(encoding="utf-8") if CORPUS.is_file() else ""
        if current != rendered:
            print(f"{CORPUS.relative_to(REPO_ROOT)} is out of date; run {Path(__file__).name}")
            return 1
        print("peer assistant corpus is current")
        return 0
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    CORPUS.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {CORPUS.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

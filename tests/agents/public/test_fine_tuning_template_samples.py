# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Template-aware sample building and LoRA targets in the agent's trainer.

Ollama sends ``enable_thinking=true`` to any model it detects as thinking-capable
unless the client says ``think: false``. A Qwen3.5 adapter trained in only one of
the two modes answered directly in that mode and returned an empty string in the
other, so the runner trains both and splits prompt from completion before
tokenizing (tokenizing the whole conversation merges ``<think>`` + newline into a
single token the model is never positioned to emit).
"""

from __future__ import annotations

import re

import pytest

from autoyou_agents.fine_tuning_agent import training_runner as runner


class _ThinkingTokenizer:
    chat_template = "{% if enable_thinking %}...{% endif %}"
    eos_token = "<|im_end|>"
    _piece = re.compile(r"<[^>]+>|\n\n|\n|\w+|[^\w\s]|[ \t]")

    def __init__(self):
        self.vocab = {}

    def encode(self, text, add_special_tokens=False):
        return [self.vocab.setdefault(piece, len(self.vocab)) for piece in self._piece.findall(text)]

    def decode(self, ids):
        inverse = {v: k for k, v in self.vocab.items()}
        return "".join(inverse[i] for i in ids)

    def apply_chat_template(self, messages, *, tokenize=False, add_generation_prompt=False, enable_thinking=None):
        assert tokenize is False
        out = ""
        for message in messages:
            if message["role"] == "assistant":
                out += f"<|im_start|>assistant\n<think>\n\n</think>\n\n{message['content']}<|im_end|>\n"
            else:
                out += f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n"
        if add_generation_prompt:
            out += "<|im_start|>assistant\n" + ("<think>\n" if enable_thinking else "<think>\n\n</think>\n\n")
        return out


def _messages(answer="Hello there friend"):
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": answer}]


def _supervised(sample, tok):
    return tok.decode([t for t, label in zip(sample["input_ids"], sample["labels"]) if label != -100])


def test_the_non_thinking_mode_supervises_the_answer_only():
    tok = _ThinkingTokenizer()
    sample = runner._build_template_sample(_messages(), tok, 256, thinking_mix=0.0)
    assert _supervised(sample, tok) == "Hello there friend<|im_end|>\n"
    assert len(sample["attention_mask"]) == len(sample["input_ids"]) == len(sample["labels"])


def test_the_thinking_mode_supervises_the_close_of_the_block_and_splits_before_the_merge():
    tok = _ThinkingTokenizer()
    sample = runner._build_template_sample(_messages(), tok, 256, thinking_mix=1.0)
    assert _supervised(sample, tok).startswith("\n</think>\n\nHello there friend")
    prompt = "<|im_start|>system\nsys<|im_end|>\n<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n<think>\n"
    joint = tok.encode(prompt + _supervised(sample, tok))
    assert sample["input_ids"] != joint  # tokenizing as one string would merge across the boundary


def test_both_modes_are_present_and_the_choice_is_stable_per_sample():
    tok = _ThinkingTokenizer()
    answers = [f"answer number {i}" for i in range(200)]
    modes = [runner._thinking_for_sample(_messages(a), 0.5) for a in answers]
    assert 60 < sum(modes) < 140
    assert modes == [runner._thinking_for_sample(_messages(a), 0.5) for a in answers]
    assert not any(runner._thinking_for_sample(_messages(a), 0.0) for a in answers)
    assert all(runner._thinking_for_sample(_messages(a), 1.0) for a in answers)
    assert runner._build_template_sample(_messages(), tok, 256) is not None  # default mix is 0.5


def test_a_template_without_a_thinking_mode_is_rendered_without_the_argument():
    class Plain(_ThinkingTokenizer):
        chat_template = "plain chatml"

        def apply_chat_template(self, messages, *, tokenize=False, add_generation_prompt=False):
            return super().apply_chat_template(messages, tokenize=tokenize, add_generation_prompt=add_generation_prompt)

    sample = runner._build_template_sample(_messages(), Plain(), 256, thinking_mix=1.0)
    assert sample is not None


def test_a_conversation_that_does_not_end_in_a_reply_is_skipped():
    tok = _ThinkingTokenizer()
    assert runner._build_template_sample(_messages()[:2], tok, 256) is None
    assert runner._build_template_sample([{"role": "assistant", "content": "x"}], tok, 256) is None


def test_long_samples_keep_the_end_of_the_prompt_and_the_start_of_the_answer():
    tok = _ThinkingTokenizer()
    long_prompt = [{"role": "user", "content": "word " * 200}, {"role": "assistant", "content": "answer " * 200}]
    sample = runner._build_template_sample(long_prompt, tok, 64, thinking_mix=0.0)
    assert len(sample["input_ids"]) <= 64 and any(label != -100 for label in sample["labels"])
    assert sample["labels"][0] == -100


def test_the_parser_exposes_the_mix_and_the_default_keeps_both_modes():
    args = runner.build_parser().parse_args(["--train-data", "t", "--output-dir", "o", "--ollama-dir", "d"])
    assert args.thinking_mix == 0.5
    assert runner.build_parser().parse_args(
        ["--train-data", "t", "--output-dir", "o", "--ollama-dir", "d", "--thinking-mix", "0"]).thinking_mix == 0.0


# ----------------------------------------------------------------- LoRA targets
def test_lora_targets_cover_linear_attention_and_skip_the_vision_tower():
    torch = pytest.importorskip("torch")
    nn = torch.nn

    class Layer(nn.Module):
        def __init__(self, hybrid):
            super().__init__()
            if hybrid:
                self.linear_attn = nn.ModuleDict({k: nn.Linear(4, 4) for k in ("in_proj_qkv", "in_proj_z", "in_proj_b", "out_proj")})
            else:
                self.self_attn = nn.ModuleDict({k: nn.Linear(4, 4) for k in ("q_proj", "k_proj", "v_proj", "o_proj")})
            self.mlp = nn.ModuleDict({k: nn.Linear(4, 4) for k in ("gate_proj", "up_proj", "down_proj")})

    model = nn.Module()
    model.language_model = nn.ModuleList([Layer(True), Layer(True), Layer(False)])
    model.visual = nn.ModuleDict({"block": nn.ModuleDict({"out_proj": nn.Linear(4, 4), "q_proj": nn.Linear(4, 4)})})
    names = runner._target_modules_for_model(model)
    leaves = [name.rsplit(".", 1)[-1] for name in names]
    assert leaves.count("in_proj_qkv") == 2 and leaves.count("in_proj_z") == 2 and leaves.count("out_proj") == 2
    assert "in_proj_b" not in leaves and not any("visual" in name for name in names)
    assert leaves.count("q_proj") == 1 and leaves.count("gate_proj") == 3

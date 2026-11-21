# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""The faster gated-delta-rule scan must be the same function as transformers' reference.

Speed is the reason it exists (a forward+backward per layer measured 512 ms
against 206 ms on a Radeon 8060S at batch 2 x 2048), and speed is worthless if
the values or gradients move. These tests compare against transformers' own
``torch_chunk_gated_delta_rule``, on the CPU, so they need no GPU.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
modeling = pytest.importorskip("transformers.models.qwen3_5.modeling_qwen3_5")

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

# Loaded by path: the module is self-contained, and this way the test also runs in
# the training venv, which has no google-adk for the autoyou_agents package.
_SPEC = importlib.util.spec_from_file_location(
    "autoyou_linear_attention",
    Path(__file__).resolve().parents[3] / "autoyou_agents" / "fine_tuning_agent" / "linear_attention.py",
)
la = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(la)

_ORIGINAL = modeling.torch_chunk_gated_delta_rule


def REFERENCE(q, k, v, g, beta, **kwargs):  # noqa: N802
    """transformers' own function. Older releases expect the caller to repeat the
    key/query heads for grouped value attention; newer ones do it inside."""
    if q.shape[2] != v.shape[2]:
        try:
            return _ORIGINAL(q, k, v, g, beta, **kwargs)
        except RuntimeError:
            ratio = v.shape[2] // q.shape[2]
            return _ORIGINAL(q.repeat_interleave(ratio, 2), k.repeat_interleave(ratio, 2), v, g, beta, **kwargs)
    return _ORIGINAL(q, k, v, g, beta, **kwargs)


def _inputs(seq_len, *, dtype=torch.float32, nk=2, nv=4, dk=16, dv=16, batch=2, seed=0):
    generator = torch.Generator().manual_seed(seed)
    make = lambda *shape: torch.randn(*shape, generator=generator, dtype=dtype)  # noqa: E731
    q = make(batch, seq_len, nk, dk).requires_grad_(True)
    k = make(batch, seq_len, nk, dk).requires_grad_(True)
    v = make(batch, seq_len, nv, dv).requires_grad_(True)
    g = (-torch.rand(batch, seq_len, nv, generator=generator) * 0.2).requires_grad_(True)
    beta = torch.rand(batch, seq_len, nv, generator=generator, dtype=dtype).requires_grad_(True)
    return [q, k, v, g, beta]


def _run(fn, inputs, **kwargs):
    for tensor in inputs:
        tensor.grad = None
    out, state = fn(*inputs, use_qk_l2norm_in_kernel=True, **kwargs)
    loss = out.float().pow(2).sum() + (state.float().pow(2).sum() if state is not None else 0)
    loss.backward()
    return out, state, [t.grad.clone() for t in inputs]


@pytest.mark.parametrize("seq_len,chunk", [(64, 64), (70, 32), (128, 64), (33, 16), (5, 64)])
def test_values_and_gradients_match_the_reference(seq_len, chunk):
    inputs = _inputs(seq_len)
    ref_out, ref_state, ref_grads = _run(REFERENCE, inputs, chunk_size=chunk)
    out, state, grads = _run(la.fast_chunk_gated_delta_rule, inputs, chunk_size=chunk)
    assert out.shape == ref_out.shape and out.dtype == ref_out.dtype
    assert torch.allclose(out, ref_out, atol=1e-6, rtol=1e-5)
    assert state is None and ref_state is None
    for name, mine, theirs in zip("q k v g beta".split(), grads, ref_grads):
        assert torch.allclose(mine, theirs, atol=1e-5, rtol=1e-4), name


def test_final_state_and_initial_state_are_honoured():
    inputs = _inputs(96)
    initial = torch.randn(2, 4, 16, 16, generator=torch.Generator().manual_seed(3))
    ref_out, ref_state, _ = _run(REFERENCE, inputs, chunk_size=32, initial_state=initial, output_final_state=True)
    out, state, _ = _run(la.fast_chunk_gated_delta_rule, inputs, chunk_size=32, initial_state=initial, output_final_state=True)
    assert torch.allclose(out, ref_out, atol=1e-6, rtol=1e-5)
    assert state.shape == (2, 4, 16, 16) and torch.allclose(state, ref_state, atol=1e-6, rtol=1e-5)


def test_bf16_inputs_come_back_in_bf16_and_agree_with_the_reference():
    inputs = _inputs(64, dtype=torch.bfloat16)
    ref_out, _, ref_grads = _run(REFERENCE, inputs, chunk_size=32)
    out, _, grads = _run(la.fast_chunk_gated_delta_rule, inputs, chunk_size=32)
    assert out.dtype == torch.bfloat16
    assert torch.equal(out, ref_out)
    for mine, theirs in zip(grads, ref_grads):
        assert torch.allclose(mine.float(), theirs.float(), atol=1e-3, rtol=1e-2)


def test_the_scan_is_linear_in_chunks_not_quadratic_in_autograd_nodes():
    """The reference indexes x[:, :, i] per chunk: its autograd graph holds a
    select (and a copy-slice) node per chunk per tensor. The fast path splits
    once, so its graph does not grow with the number of select nodes."""
    def nodes(fn, seq_len):
        inputs = _inputs(seq_len)
        out, _ = fn(*inputs, chunk_size=16, use_qk_l2norm_in_kernel=True)
        seen, stack, names = set(), [out.grad_fn], []
        while stack:
            node = stack.pop()
            if node is None or node in seen:
                continue
            seen.add(node)
            names.append(type(node).__name__)
            stack.extend(next_fn for next_fn, _ in node.next_functions)
        return names

    reference = nodes(REFERENCE, 160)
    fast = nodes(la.fast_chunk_gated_delta_rule, 160)
    select_like = lambda names: sum(n in {"SelectBackward0", "CopySlices"} for n in names)  # noqa: E731
    assert select_like(reference) >= 10 * max(1, select_like(fast)) or select_like(fast) == 0
    assert select_like(fast) < select_like(reference)


# ------------------------------------------------------------------- install
def test_install_swaps_the_function_and_is_idempotent(monkeypatch):
    monkeypatch.setattr(modeling, "torch_chunk_gated_delta_rule", _ORIGINAL)
    monkeypatch.setattr(la.importlib.util, "find_spec", lambda name, *a, **k: None)
    first = la.install()
    assert "transformers.models.qwen3_5.modeling_qwen3_5" in first["patched"]
    assert modeling.torch_chunk_gated_delta_rule is la.fast_chunk_gated_delta_rule
    second = la.install()
    assert second["patched"] and modeling.torch_chunk_gated_delta_rule is la.fast_chunk_gated_delta_rule


def test_install_leaves_a_real_fla_kernel_alone(monkeypatch):
    monkeypatch.setattr(modeling, "torch_chunk_gated_delta_rule", _ORIGINAL)
    monkeypatch.setattr(la.importlib.util, "find_spec", lambda name, *a, **k: object() if name == "fla" else None)
    result = la.install()
    assert result["patched"] == [] and "flash-linear-attention" in result["skipped"]
    assert modeling.torch_chunk_gated_delta_rule is _ORIGINAL
    forced = la.install(force=True)
    assert forced["patched"]


def test_a_patched_layer_computes_the_same_forward_as_an_unpatched_one(monkeypatch):
    """End to end through a real (tiny) Qwen3.5 text model, not just the function."""
    transformers = pytest.importorskip("transformers")
    config_cls = getattr(transformers, "Qwen3_5TextConfig", None)
    model_cls = getattr(transformers, "Qwen3_5ForCausalLM", None)
    if config_cls is None or model_cls is None:
        pytest.skip("this transformers build has no Qwen3.5 text model")
    config = config_cls(
        vocab_size=128, hidden_size=64, intermediate_size=128, num_hidden_layers=4, num_attention_heads=4,
        num_key_value_heads=2, head_dim=16, linear_num_key_heads=2, linear_num_value_heads=4,
        linear_key_head_dim=16, linear_value_head_dim=16, layer_types=["linear_attention"] * 3 + ["full_attention"],
        max_position_embeddings=256,
    )
    torch.manual_seed(0)
    model = model_cls(config).eval()
    ids = torch.randint(0, 128, (2, 70))

    monkeypatch.setattr(modeling, "torch_chunk_gated_delta_rule", _ORIGINAL)
    with torch.no_grad():
        expected = model(input_ids=ids).logits
    monkeypatch.setattr(la.importlib.util, "find_spec", lambda name, *a, **k: None)
    la.install()
    with torch.no_grad():
        actual = model(input_ids=ids).logits
    assert torch.allclose(actual, expected, atol=1e-5, rtol=1e-4)

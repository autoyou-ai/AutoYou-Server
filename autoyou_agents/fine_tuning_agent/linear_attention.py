# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""A faster, equivalent reference path for gated linear attention (Qwen3.5 / Qwen3-Next).

Three of every four layers of the Qwen3.5 family are gated delta-rule linear
attention. The fast implementation is a Triton kernel from ``flash-linear-
attention`` (``fla``), which exists for CUDA and not for the ROCm-on-Windows,
Intel XPU, Apple MPS or CPU builds this agent also trains on. Those fall back to
a PyTorch reference in transformers that is correct and, on the gfx1151 machine
this was measured on, spends about 470 ms per layer on a forward and backward
at batch 2 x 2048 tokens - six times its forward - so the 24 linear layers of a
4B model cost ~11 s of every ~18 s micro-batch.

The cost is not arithmetic (a layer is a few GFLOP) and not kernel launches. It is
how the reference reads its inputs: inside the chunk loop it indexes
``x[:, :, i]`` on five tensors of ~67 MB. Autograd's backward for an index is a
full-size zero tensor plus a full-size add into the gradient, once per chunk, so
the backward moves ``chunks x tensor`` bytes (a profile shows ``add_``,
``fill_``, ``copy_`` and ``select_backward`` dominating) instead of one tensor's
worth. The outputs have the same problem in reverse: ``core_attn_out[:, :, i] = ...``
is a ``CopySlices`` per chunk.

This module computes the same function with the inputs split once (``unbind``)
and the outputs joined once (``stack``), so the backward is linear in the
tensor, not in chunks x tensor. The arithmetic is unchanged; the test suite
compares values and gradients against transformers' own reference.

The second cost is the short causal convolution in front of the scan. Without the
``causal_conv1d`` package transformers runs it as a grouped ``F.conv1d`` with one
group per channel (8192 of them in the 4B model), which the ROCm and MIOpen
depthwise path executes at ~19 ms forward+backward per layer for 1024 tokens -
as much as the scan itself, about a fifth of a whole training step. A kernel
of width four is four shifted multiply-adds; :func:`fast_causal_conv1d_fn` does
exactly that and measures ~6 ms for the same layer, with an error against an
fp32 reference of the same order as the convolution's own bf16 rounding.

:func:`install` swaps each function in only when its real kernel is not
available (``fla`` for the scan, ``causal_conv1d`` for the convolution), so a
CUDA host that has the real kernels keeps them.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import importlib
import importlib.util
from typing import Any, Dict, Optional, Tuple

#: Transformers modules that define the reference function with this signature.
_MODEL_MODULES = (
    "transformers.models.qwen3_5.modeling_qwen3_5",
    "transformers.models.qwen3_5_moe.modeling_qwen3_5_moe",
    "transformers.models.qwen3_next.modeling_qwen3_next",
)
_REFERENCE_NAME = "torch_chunk_gated_delta_rule"
_INSTALLED_MARK = "_autoyou_fast_gated_delta_rule"
_CONV_NAME = "causal_conv1d_fn"
_CONV_MARK = "_autoyou_fast_causal_conv1d"


def fast_causal_conv1d_fn(
    hidden_states: Any,
    weight: Any,
    bias: Optional[Any] = None,
    activation: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Same inputs, outputs and arithmetic as transformers' fallback ``causal_conv1d_fn``.

    ``hidden_states`` is ``[B, C, L]``, ``weight`` is ``[C, K]`` (one filter per
    channel) and the result is ``[B, C, L]``: the causal depthwise convolution,
    then ``activation``. Computed as ``K`` shifted multiply-adds over the
    left-padded input rather than as a grouped convolution; autograd's backward is
    the matching handful of elementwise ops. Extra keyword arguments (``seq_idx``
    and the like, meant for the fused kernel) are ignored, as the fallback does.
    """
    import torch.nn.functional as F

    seq_len = hidden_states.shape[-1]
    kernel = weight.shape[-1]
    padded = F.pad(hidden_states.to(weight.dtype), (kernel - 1, 0))
    out = padded[..., :seq_len] * weight[:, 0:1]
    for shift in range(1, kernel):
        out = out + padded[..., shift:shift + seq_len] * weight[:, shift:shift + 1]
    if bias is not None:
        out = out + bias[:, None]
    if activation is not None:
        try:
            from transformers.activations import ACT2FN

            out = ACT2FN[activation](out)
        except (ImportError, KeyError):
            out = getattr(F, activation)(out)
    return out.to(hidden_states.dtype)


def fast_chunk_gated_delta_rule(
    query: Any,
    key: Any,
    value: Any,
    g: Any,
    beta: Any,
    chunk_size: int = 64,
    initial_state: Optional[Any] = None,
    output_final_state: bool = False,
    use_qk_l2norm_in_kernel: bool = False,
    **kwargs: Any,
) -> Tuple[Any, Optional[Any]]:
    """Same inputs, outputs and arithmetic as transformers' ``torch_chunk_gated_delta_rule``.

    Shapes: query/key ``[B, L, Hk, Dk]``, value ``[B, L, Hv, Dv]``, g and beta
    ``[B, L, Hv]``. Returns ``([B, L, Hv, Dv], final_state_or_None)``.
    """
    import torch
    import torch.nn.functional as F

    initial_dtype = query.dtype
    batch_size, sequence_length, num_k_heads, k_head_dim = key.shape
    num_v_heads, v_head_dim = value.shape[-2:]
    recurrent_state_shape = (batch_size, num_v_heads, k_head_dim, v_head_dim)
    decay = g

    if num_k_heads != num_v_heads:
        query = query.repeat_interleave(num_v_heads // num_k_heads, dim=2)
        key = key.repeat_interleave(num_v_heads // num_k_heads, dim=2)

    query, key, value, beta, decay = [
        x.transpose(1, 2).to(torch.float32, memory_format=torch.contiguous_format)
        for x in (query, key, value, beta, decay)
    ]
    if use_qk_l2norm_in_kernel:
        query = query * torch.rsqrt((query * query).sum(dim=-1, keepdim=True) + 1e-6)
        key = key * torch.rsqrt((key * key).sum(dim=-1, keepdim=True) + 1e-6)
    query = query * (query.shape[-1] ** -0.5)

    pad_size = (chunk_size - sequence_length % chunk_size) % chunk_size
    query, key, value = (F.pad(x, (0, 0, 0, pad_size)) for x in (query, key, value))
    beta, decay = (F.pad(x, (0, pad_size)) for x in (beta, decay))
    num_chunks = (sequence_length + pad_size) // chunk_size

    v_beta = value * beta.unsqueeze(-1)
    k_beta = key * beta.unsqueeze(-1)
    query, key, k_beta, v_beta = [
        x.reshape(x.shape[0], x.shape[1], -1, chunk_size, x.shape[-1]) for x in (query, key, k_beta, v_beta)
    ]
    decay = decay.reshape(decay.shape[0], decay.shape[1], -1, chunk_size)

    strictly_upper_mask = torch.ones(chunk_size, chunk_size, dtype=torch.bool, device=query.device).triu(1)
    cum_decay = decay.cumsum(dim=3)

    pairwise_decay = cum_decay.unsqueeze(4) - cum_decay.unsqueeze(3)
    pairwise_decay = pairwise_decay.masked_fill(strictly_upper_mask, float("-inf")).exp()

    ut_system = (k_beta @ key.transpose(-1, -2)) * pairwise_decay
    intra_chunk_attn = (query @ key.transpose(-1, -2)) * pairwise_decay
    decayed_k_beta = k_beta * cum_decay.exp().unsqueeze(-1)

    new_values = torch.linalg.solve_triangular(ut_system, v_beta, upper=False, unitriangular=True)
    k_cumdecay = torch.linalg.solve_triangular(ut_system, decayed_k_beta, upper=False, unitriangular=True)

    if initial_state is None:
        state = torch.zeros(recurrent_state_shape, dtype=new_values.dtype, device=new_values.device)
    else:
        state = initial_state.to(new_values)

    query = query * cum_decay.exp().unsqueeze(-1)
    key = key * (cum_decay[..., -1:] - cum_decay).exp().unsqueeze(-1)
    chunk_decay = cum_decay[..., -1].exp()[..., None, None]

    # The scan. Split every per-chunk input ONCE: unbind's backward is a single
    # stack of gradients, where indexing x[:, :, i] inside the loop makes autograd
    # materialise a full-size zero tensor and add it into the gradient per chunk.
    new_values_c = new_values.unbind(2)
    k_cumdecay_c = k_cumdecay.unbind(2)
    query_c = query.unbind(2)
    intra_c = intra_chunk_attn.unbind(2)
    key_c = key.unbind(2)
    chunk_decay_c = chunk_decay.unbind(2)

    outputs = []
    for i in range(num_chunks):
        v_new = new_values_c[i] - k_cumdecay_c[i] @ state
        outputs.append(query_c[i] @ state + intra_c[i] @ v_new)
        state = state * chunk_decay_c[i] + key_c[i].transpose(-1, -2) @ v_new

    # ...and join the outputs once, rather than a CopySlices per chunk.
    core_attn_out = torch.stack(outputs, dim=2)
    final_state = state if output_final_state else None
    core_attn_out = core_attn_out.reshape(batch_size, num_v_heads, -1, v_head_dim)[:, :, :sequence_length]
    core_attn_out = core_attn_out.transpose(1, 2).to(initial_dtype, memory_format=torch.contiguous_format)
    return core_attn_out, final_state


def _swap(module: Any, attribute: str, replacement: Any, mark: str) -> bool:
    """Put ``replacement`` in place of ``module.attribute``; True when it is in place afterwards."""
    current = getattr(module, attribute, None)
    if current is None:
        return False
    if getattr(current, mark, False):
        return True
    setattr(replacement, mark, True)
    setattr(module, attribute, replacement)
    return True


def install(*, force: bool = False) -> Dict[str, Any]:
    """Replace transformers' reference scan and short convolution with the faster equivalents.

    The scan is replaced by :func:`fast_chunk_gated_delta_rule` unless an ``fla``
    kernel is available, the convolution by :func:`fast_causal_conv1d_fn` unless
    ``causal_conv1d`` is (CUDA hosts keep the real kernels), both unless ``force``.
    Safe to call repeatedly. Returns what it did, for the job log: ``patched``
    lists the model modules whose scan was replaced, ``conv_patched`` those whose
    convolution was.
    """
    result: Dict[str, Any] = {"patched": [], "conv_patched": [], "skipped": ""}
    patch_scan = force or importlib.util.find_spec("fla") is None
    patch_conv = force or importlib.util.find_spec("causal_conv1d") is None
    if not patch_scan:
        result["skipped"] = "flash-linear-attention is installed; its kernel is used"
    for name in _MODEL_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        if patch_scan and _swap(module, _REFERENCE_NAME, fast_chunk_gated_delta_rule, _INSTALLED_MARK):
            result["patched"].append(name)
        if patch_conv and _swap(module, _CONV_NAME, fast_causal_conv1d_fn, _CONV_MARK):
            result["conv_patched"].append(name)
    if patch_scan and not result["patched"]:
        result["skipped"] = "no gated-delta-rule model module is importable"
    return result

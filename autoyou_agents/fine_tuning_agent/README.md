# Fine Tuning Agent

Fine Tuning turns an already private dataset into a local LoRA training job. It
does not collect account history itself: Data Collector owns local conversation,
WhatsApp, and consented Telegram Saved Messages collection, then hands over a
private export. Fine Tuning owns folder/upload/handoff import, dataset
preparation, training, artifact conversion, and explicit Ollama installation.

The managed website is available through the authenticated Page Service at
`/agent/fine_tuning_agent/`. Its direct backend listens on loopback port `8068`;
do not expose that port publicly.

## `tuning` versus `training-full`

`tuning` is the optional ML dependency component: Torch, Transformers,
Datasets, PEFT, Accelerate, and related trainer packages. It is deliberately
separate so the website can import and prepare datasets on a host that cannot
or should not install a large training stack.

`training-full` is the complete AutoYou runtime profile plus that component.
Use it when a source environment or compiled package must actually run Fine
Tuning jobs:

```powershell
py -3 scripts\bootstrap_autoyou.py --profile training-full
.\servers\windows\build-all.ps1 -ReleaseProfile training-full
```

```bash
./servers/macos/build-all.sh --requirements training-full
./servers/wsl/build-backend.sh --include-tuning
docker build --build-arg AUTOYOU_INCLUDE_TUNING=1 -f Dockerfile .
docker build --build-arg AUTOYOU_INCLUDE_TUNING=1 -f Dockerfile.compiled-linux .
```

The WSL flag is named separately because its normal build starts from the full
server requirements and optionally adds the trainer component. The Windows and
macOS package profiles express the same combined result.

## Compiled runtime contract

Windows, macOS, and WSL packages compile the agent Python modules to native
`.pyd`/`.so` files. The website files and Data Collector's Node history worker
remain read-only runtime assets and are included in the package integrity
manifest. Platform build scripts verify the website backend, agent chat facade,
required assets, and (for a training-enabled package) trainer imports.

The protected training worker cannot be launched with `python -m` from its
compiled extension. In a package it re-enters the fixed AutoYou launcher mode
`--run-fine-tuning-runner`, which imports only the allowlisted compiled runner.
This keeps agent Python source out of the package while preserving normal
subprocess training behavior. Source-mode development still uses the usual
module command.

The ordinary Dockerfiles intentionally copy source and are for local/private
development. Use `Dockerfile.compiled-linux` when a Linux container needs the
same source-protected compiled runtime boundary.

## Jobs, chat, and safe stop

The website starts jobs through the managed backend. Agent chat exposes dataset
preparation, job status, start, and `cancel_fine_tuning_job`; natural-language
stop requests also resolve the active job. `POST /api/jobs/{job_id}/cancel`
persists a `cancelling` state in the agent SQLite store, so a website process
and AI-agent process can coordinate without sharing an in-memory process id.
The worker terminates the child trainer, kills it after a short grace period if
needed, marks the job `cancelled`, and skips artifact conversion after a
cancellation.

## Hardware

`hardware.py` answers "how much can a LoRA job safely use on this machine",
for hardware that does not agree on what memory means. It reads whatever torch
the interpreter has (nothing is imported at module load) and names the family:

| Family | Backend | How its budget is read |
| --- | --- | --- |
| NVIDIA consumer / datacenter | `cuda` | free VRAM from `mem_get_info`, 8% margin |
| NVIDIA GB10 (DGX Spark), Jetson | `cuda` | free unified memory x 0.85 |
| AMD Radeon / Instinct | `rocm` | free VRAM, 8% margin |
| AMD Strix Halo (gfx1150-1153) | `rocm` | reported total x 0.75 (a process runs out well below the dedicated + shared sum) |
| Intel Arc / Data Center GPU | `xpu` | free device memory |
| Intel integrated graphics | `xpu` | shared pool x 0.5 |
| Apple silicon | `mps` (+ `mlx`) | Metal's recommended working set x 0.9 |
| Nothing | `cpu` | free RAM x 0.36; LoRA is opt-in |

`AUTOYOU_FINE_TUNING_MEMORY_GB` overrides the figure when the operator knows
something the probe cannot (for example how much of a unified pool firmware
reserved for the GPU).

CPU LoRA stays opt-in (`AUTOYOU_FINE_TUNING_ALLOW_CPU_TRAINING=1` or the job
option). Intel Gaudi, Huawei Ascend and TPUs are not supported.

The hardware, the model and the dataset decide the plan (`planner.py`):
executor, precision (bf16 / fp16 / fp32 / 4-bit QLoRA on CUDA with
bitsandbytes), micro-batch and activation checkpointing, attention kernel,
loader workers and the allocator environment. Expandable segments are set for
CUDA only: on a unified-memory AMD APU the same setting stopped runs at a fixed
30 GiB. Estimates are lower bounds with a per-family calibration; the real
safeguard is the repair loop below.

### Apple silicon: MLX

Text datasets on Apple silicon train with `mlx-lm` (`mlx_executor.py`), which is
faster and lighter than Torch's MPS backend. The finished adapter is converted
to the Hugging Face PEFT layout by an exact transpose (`lora_a`/`lora_b` to
`lora_A`/`lora_B`, `alpha = scale * rank`), so the GGUF-adapter conversion and
Ollama `ADAPTER` install treat it like a Torch job. Image datasets still use
Torch on MPS. The command, data layout, log parsing and conversion are
unit-tested; the first run on a Mac is the integration test, and has not
happened on this repository's test machine.

## Base models and licences

A fine-tune inherits its base's licence, and AutoYou hosts and distributes tuned
weights. `model_registry.py` records each catalogued base's licence and
`license_tier` gates it: `permissive` (Apache-2.0, MIT, BSD), `non_commercial`
(Qwen Research License - which covers Qwen2.5 3B and 72B while the other Qwen2.5
sizes are Apache-2.0 - and CC-BY-NC), `custom`, or `unknown`.
`recommend_base` chooses by usable memory and only returns permissive bases by
default (Qwen3.5 0.8B / 2B / 4B / 9B). Models outside the catalog report
`unknown` until their model card is read (`allow_network`), and nothing is
looked up over the network silently.

## Goal-driven training

`training_goal.py` is the training counterpart of
`self_improvement/goal_loop.py`: state a goal, attempt it, check the result
against evidence, repair what failed, and write a report either way.

```
probe -> plan -> train (attempt -> verify -> repair)* -> install
```

- **Verification is against evidence.** A job reporting `completed` is not
  trusted: `adapter_model.safetensors` must exist and hold finite numbers, the
  losses must be finite, and an optional `target_eval_loss` must be met (a miss
  earns one longer attempt).
- **Repair, not retry.** A failure is classified and each class changes
  something different. Out of memory enables checkpointing, then halves the
  micro-batch, then the sequence length, then switches to 4-bit, then cuts the
  rank. A non-finite loss halves the learning rate and tightens clipping, then
  tries fp32. A trainer that vanished with no traceback (an external
  `taskkill`, the OS) is *resumed from its newest checkpoint with the same
  plan*, because nothing about the plan was wrong.
- **State is on disk.** Every transition is persisted under `goal_runs/`, so
  `resume_training_goal` continues from the first incomplete stage after a
  pause, a failure or a server restart. `pause` keeps the work; `cancel` stops
  the trainer and marks the goal finished.
- **Real progress.** The trainer emits `AUTOYOU_PROGRESS {json}` lines (step,
  loss, seconds per step, ETA, peak memory); the job supervisor reads them
  instead of drawing a bar from elapsed time.

Agent tools: `get_training_hardware_report`, `check_base_model_license`,
`start_training_goal`, `get_training_goal`, `list_training_goal_runs`,
`pause_training_goal`, `resume_training_goal`, `cancel_training_goal`.
`start_fine_tuning_job` remains for a single job with explicit settings.

## Trainer behaviour worth knowing

- Models are loaded straight to the device, not through `device_map="auto"`
  (which can offload layers to the CPU without saying so).
- `trust_remote_code` is off; official Qwen, Mistral and Gemma repositories need
  none. Pass `--trust-remote-code` only for a model that requires it.
- LoRA targets are resolved from the loaded model and include the gated
  linear-attention projections of hybrid models (`in_proj_qkv`, `in_proj_z`,
  `out_proj`), which the classic `q/k/v/o` list leaves frozen.
- For templates with a thinking mode (Qwen3.5) the final reply is supervised in
  non-thinking mode and the prompt and completion are tokenized separately, as
  generation does, so training never teaches a token the model cannot emit.
- Checkpoints are saved every 50 steps so a killed job can resume.
- Hybrid models (Qwen3.5) run three of every four layers as gated linear attention.
  Where the CUDA-only `fla` / `causal_conv1d` kernels are absent (ROCm on Windows,
  Intel XPU, Apple MPS, CPU) `linear_attention.py` swaps in faster, value- and
  gradient-identical versions of the PyTorch scan (2.5x) and the short causal
  convolution; set `AUTOYOU_FAST_LINEAR_ATTENTION=0` to use transformers' own.
- The progress line (`AUTOYOU_PROGRESS`) reports seconds per step counted from the end
  of the first step, so warm-up is not averaged into the ETA.

Capability status reports missing dependencies and unavailable hardware rather
than silently pretending a host can train. It never returns raw dataset
messages in job/status responses.

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

## Hardware expectations

- CUDA is the preferred NVIDIA path.
- ROCm works when the installed Torch build exposes the supported AMD device.
- Apple Silicon uses Torch MPS when available.
- CPU LoRA is intentionally opt-in (`AUTOYOU_FINE_TUNING_ALLOW_CPU_TRAINING=1`
  or the corresponding job option) and is best reserved for small models.
- MLX/`mlx-lm` is detected on Apple Silicon and reported in capability status,
  but it is not yet the executor for the Torch-to-Ollama training/install path.

Capability status reports missing dependencies and unavailable hardware rather
than silently pretending a host can train. It never returns raw dataset
messages in job/status responses.

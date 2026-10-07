# Local intent routing

Decision and validation notes, 23 September 2026.

## Model and license decision

| Candidate | Publisher's license | Fit for AutoYou |
| --- | --- | --- |
| [openjev/openjev](https://huggingface.co/openjev/openjev) | CC BY-NC 4.0 | Noncommercial terms exclude the commercial default. 27B parameters also exceed the intended phone budget. Not downloaded or bundled. |
| [AlexWortega/openjev](https://huggingface.co/AlexWortega/openjev) | Model-card metadata says MIT | A separate Qwen3.5 NLI classifier, not the model above. Its smallest 0.8B checkpoint is about 1.7 GB. The inspected revision has no standalone LICENSE/NOTICE file, and the card warns about prompt injection. Not bundled; confirm distribution terms and evaluate on devices before adopting. |
| [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | Apache 2.0 | Selected: 22.7M parameters; the publisher's quantized ONNX file is 23,026,053 bytes. Runs in process on CPU without Ollama or a server. |
| [LFM2.5-350M-GGUF](https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF) | LFM Open License v1.0 | Bundled mobile reply model (QAD Q4_0). Model license and exact pinned revision accompany the asset. |
| [Gemma 3 270M](https://huggingface.co/google/gemma-3-270m-it) | [Gemma terms](https://ai.google.dev/gemma/terms) | Small generation candidate requiring its own distribution-terms review and quality evaluation. Not bundled. |
| [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B) | Apache 2.0 | Higher-capability mobile candidate, but 2.3B effective / 5.1B including embeddings is a different memory tier from a 0.3B default. Not bundled. |

Apache 2.0 permits commercial use subject to its conditions. The model card,
license, attribution, and ONNX Runtime notices accompany the packaged assets.
The release dependency policy now includes the MIT ONNX Runtime for this text
classifier; exclusions for wake-word models and the optional speech stack remain.
`assets/intent_router/manifest.json` pins the publisher revision, file lengths,
and SHA-256 hashes. Build preparation verifies those hashes and fails on a
mismatch. Model weights are build data, not committed source or downloaded code.

## What is implemented

- Python's existing root callback uses a confident classification only after
  explicit selections, existing deterministic routes, and follow-up handling.
  It dispatches through the existing ADK specialist tools and permission checks.
- Swift and Kotlin execute the same model and routing profile locally. Peer
  assistants add an advisory topic to the selected reply engine's instructions;
  the original instructions and conversation prompt remain intact.
- v2 uses the same Python routing path and bundles the verified assets. The
  native Swift package also supplies the router for native peer assistants.
- Fine Tuning Agent can inspect model readiness and classify an example without
  Ollama. These tools do not train or modify classifier weights.
- Missing assets or an unavailable runtime leave the selected reply engine in
  control. Generic Python imports do not require Apple's frameworks or ONNX.

The existing ADK callback supplies the classification stage, so another agent
or graph-workflow dependency is unnecessary. The linked
[ADK discussion](https://github.com/google/adk-python/discussions/2593) is useful
background, not an implementation or correctness guarantee.

## Limits

MiniLM is an English sentence-embedding encoder. It is not an instruction
follower, generative language model, policy judge, or permission guard. Similarity
scores are not probabilities. It cannot reason over an entire conversation or
system prompt. Requests over 256 wordpieces, short ambiguous follow-ups,
negation/conditional language, and uncertain scores defer to the full-context
reply model. Availability is checked after comparison with all route candidates.

This does not unlock internal capabilities of another model. It also does not
embed Python's complete `autoyou_agents` catalog on phones. Native mobile tool
implementations, OS permissions, and a suitable reply model are still required
for those capabilities. In-process inference alone does not establish compliance
with [App Store review requirements](https://developer.apple.com/app-store/review/guidelines/#software-requirements).

## Build and checks

```sh
python3 scripts/prepare_intent_router.py
python3 -m pip install -r requirements/local-llm.txt
AUTOYOU_TEST_ROOT="$(mktemp -d)" AUTOYOU_TEST_INTENT_MODEL=1 \
  python3 -m pytest tests/server/runtime/test_intent_router.py -q
```

The iOS build phase, Android `preBuild`, v2 macOS builder, native server builders,
and Docker builds prepare the data automatically. Source bootstrap prepares it
when installing the `local-llm` component. Source deployments that skip setup
must run the preparation command; application runtime never downloads it. Set
`AUTOYOU_LOCAL_ROUTING=0` to disable Python fast routing, or
`AUTOYOU_INTENT_ROUTER_DIR` to select an explicitly prepared bundle directory.

Shared synthetic vectors check tokenization, positive routes, and abstention in
Python, Swift, and Kotlin. Short inference measured about 1–3 ms after a roughly
370 ms initial load on this M4; this is neither a phone benchmark nor a general
classification accuracy result.

### Validation results

The required `python scripts/e2e_validate.py changed` gate exited successfully
with a fresh `AUTOYOU_TEST_ROOT` and a separate server using synthetic credentials.
Evidence: `output/e2e-evidence/run-20260923-212447/RUN_REPORT.md`.

| Check | Result |
| --- | --- |
| Python server / Python clients | 2,574 / 649 tests passed |
| Android unit tests | 475 passed |
| Shared Swift Peer Link package | 82 passed, including actual-model vectors |
| v2 Python / Swift | 87 passed and 1 skipped / 30 passed |
| License and release policy checks | 69 passed |
| iOS main / iOS 16 targets | Simulator builds passed; iOS 16 target compiled for arm64 and x86_64 |
| Android packaging | Debug and minified benchmark builds passed; packaged model hash and retained ONNX JNI classes checked |
| Real server connection | Encrypted WebRTC text, agent routing, image-to-website, speech recognition and audible speech reply passed |
| Native classifier over Peer Link | Actual packaged inference passed in both iOS Simulator and Android Emulator; original owner instructions and incoming message preserved |
| Registry screen scenarios | 7 passed, 0 failed, 10 skipped: Android bottom navigation could not be observed by the UI harness |

The native classifier check is qualified during release builds with installed debug apps in isolated
simulators/emulators (using internal release runner harnesses):

```sh
# Qualification commands executed during release validation:
# python -m pytest tests/server/build/ -q
# python -m pytest tests/agents/public/ -q
```

MiniLM runs inside the actual app in these checks. Only the HTTP reply engine is
a synthetic fixture: it asserts the routing hint and original instructions, then
returns a labelled fixture response. This proves inference and Peer Link delivery,
not generative-model quality or physical-phone performance. Existing live Apple
Intelligence and server reply-engine checks are separate.

After the full gate, 35 focused classifier, speech and native-harness checks
passed again; v2's 87 tests passed again against the newer `main` commits.

The macOS build is checkout-backed development packaging. Windows/Linux binary
builds, physical-phone latency, and the skipped Android UI scenarios remain
unverified. Build, transport, and unit-test results do not establish full device
or feature parity.

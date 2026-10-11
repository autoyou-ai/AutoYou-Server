# AutoYou Third-Party Notices

Last updated: 2026-10-10

This file is the repository-level notice index for AutoYou. It does not replace the exact license texts that must be included with final release artifacts.

## Primary Notice Page

The public attributions page is at [www.autoyou.me/attributions](https://www.autoyou.me/attributions/). OpenStorey commits at least 15% of qualifying net receipts to approved contributors, and the open-source developers credited there are welcome to take part in the same way; see the [Open-Source Commitment](docs/legal/open-source-commitment.md).

## Contributed Files Under Another License

A new file written by an outside contributor may carry its own permissive license (MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, or ISC) when a maintainer approved it in the linked issue; see [CONTRIBUTING.md](CONTRIBUTING.md#ownership-and-licensing). Each such file is listed here with its path, license, and author, and the maintainer records it in `docs/legal/release-artifacts.json` so it appears in the artifact NOTICE and SBOM. None so far.

## Release Artifact Requirement

Every official distribution should include an artifact-specific notice bundle covering only what it ships:

- AutoYou server main package
- AutoYou Server Windows compiled package
- AutoYou Server macOS compiled package
- AutoYou Server Linux/WSL compiled package

The notice bundle should include:

- AutoYou Source-Available Personal-Use License.
- Dependency licenses and notices for bundled libraries.
- Apache 2.0 NOTICE obligations where applicable.
- BSD/ISC/MIT notices where applicable.
- LGPLv3 / LGPL-2.1-or-later notices and relinking/reverse-engineering rights where LGPL components are bundled.
- GPLv3 and AGPL-3.0 source/offer notices only for optional separate GPL/AGPL services or research tools that are actually distributed.
- Vendor SDK terms notices for Apple, Google, Microsoft, Azure, GIPHY, WebRTC binaries, and similar platform SDKs.

## Google ADK Web

Google's Agent Development Kit Web frontend and Python distribution are
distributed under Apache-2.0 ([ADK Web license](https://github.com/google/adk-web/blob/main/LICENSE);
[ADK Python license](https://github.com/google/adk-python/blob/main/LICENSE)).
AutoYou modifies
only staged or AutoYou-owned writable copies of the frontend, removing the
Usage Metrics control and first-run telemetry consent UI. The upstream
development-use disclosure remains visible. Modified JavaScript and HTML
files carry notices describing the changes; release Legal folders include
the full Apache-2.0 text as google-adk-APACHE-2.0.txt. This descriptive
reference does not imply Google approval or endorsement.

This frontend-only change stops the ADK Web UI consent and settings requests;
it does not disable separate ADK CLI telemetry, explicitly configured
OpenTelemetry exporters, or provider-side logging.

Google's upstream ADK Web README also labels the feature Preview and points to
the [Google Cloud Pre-GA Offerings Terms](https://cloud.google.com/terms/service-terms).
The upstream development-use disclosure remains visible in the UI. The Apache
license for the frontend does not override any separate service terms; review
the terms applicable to the Google services and account used with a deployment.

The patch uses semantic anchors without a hard-coded ADK version. A recognized
control that moves into an unsupported structure causes packaging to fail. A
future release that renames or removes all recognition markers still requires
manual UI review before changing the Google ADK dependency pin. Review the new
frontend behavior, license files, and upstream notices; update the sanitizer
only after that review. This does not promise compatibility with every future
ADK release.

## MCP SDK 2.x runtime dependencies

The current source pins Google ADK 2.11.0, MCP SDK and mcp-types 2.3.0,
and httpx2/httpcore2 2.13.1. MCP and mcp-types retain the MIT license and
Anthropic copyright notices. httpx2 and httpcore2 retain BSD-3-Clause license
texts and upstream copyright notices for Pydantic Services, Encode, and
contributors. These licenses apply to their respective third-party code;
AutoYou's source-available license does not replace them.

The exact texts are retained in `docs/legal/licenses`/ and copied into release bundles
whose source inventory includes these components:

| Component | License file |
| --- | --- |
| Google ADK | `google-adk-APACHE-2.0.txt` |
| MCP SDK | `mcp-MIT.txt` |
| mcp-types | `mcp-types-MIT.txt` |
| httpx2 | `httpx2-BSD-3-Clause.txt` |
| httpcore2 | `httpcore2-BSD-3-Clause.txt` |
| ADK Web runtime and embedded dependencies | `google-adk-web-THIRD-PARTY-NOTICES.txt` |

The ADK 2.11 frontend matches ADK Web v1.0.7. Its conservative lockfile notice
collection includes Angular and other frontend dependencies, plus embedded
Graphviz 15.1.1 under EPL-2.0 with source availability links, Expat, Emscripten,
and musl. Original notices and available author attributions are retained;
standard MIT/ISC text is explicitly identified where upstream supplies a license
declaration but no standalone license file. This notice inventory can include
unused packages and is not a complete binary SBOM.

Names describe the components used and do not imply vendor endorsement.
Source inventories describe dependency manifests. Existing published package
SBOMs retain the versions recorded from those packages until a new release is built.

## Current Known Special Cases

- The offline intent router bundles `sentence-transformers/all-MiniLM-L6-v2` under Apache-2.0 and uses Microsoft's ONNX Runtime under MIT. The pinned model card, attribution, Apache license, runtime license, and runtime third-party notices are in `assets/intent_router/` and accompany the model in mobile, desktop, and server packages. The AutoYou routing profile is separate from the unmodified model weights.
- `signal-cli` is GPLv3 and must remain an optional separate service unless AutoYou is prepared to satisfy GPLv3 obligations for a combined distribution.
- `signal-cli-rest-api` is used as a separate Docker service and has its own notices.
- `wwebjs-api` (`avoylenko/wwebjs-api`, Apache-2.0 / MIT) is an optional Docker container REST API service alternative to the Node.js WhatsApp subprocess (`whatsapp_docker_service.py`). It is used in standalone and compiled server builds (Nuitka binary packages on Windows, macOS, and Linux) to avoid Node native module bundling issues. WhatsApp integration remains owner-only and user-opted.
- `auto-browser` suite (`auto-browser-client`, `auto-browser-langchain`, `auto-browser-mcp` from `https://github.com/LvcidPsyche/auto-browser`, MIT-licensed) is used by `autoyou_agents/browser_agent` to provide controller-backed, real-browser automation and MCP stdio tool execution.
- `cognee` (`https://github.com/topoteretes/cognee`, Apache-2.0) and `instructor` (MIT) provide optional graph and vector memory capabilities in high-resource agent profiles (`requirements/cognee.txt`).
- `tunnelmole` npm client is MIT-licensed, but the self-hosted Tunnelmole Service used by VM2 is AGPL-3.0 unless AutoYou has a separate commercial license. Do not treat the npm client notice as covering the self-hosted service. VM2 release/deploy artifacts need the service license text, corresponding-source/commercial-license evidence, and SBOM/SCA before enterprise production use.
- `undetected-chromedriver` is GPLv3 in research-only requirements and must not be included in official commercial bundles unless approved by counsel.
- `pystray` and `python-telegram-bot` are LGPLv3 and require binary-distribution compliance if bundled.
- `zeroconf` (python-zeroconf) is LGPL-2.1-or-later and ships in server and desktop builds for Lobby discovery (`requirements/base.txt`). Binary bundles need the LGPL notice, a written source offer or the unmodified source, and must leave the module replaceable; release NOTICE files already record it.
- `qrcode` is BSD-3-Clause. Its package metadata also carries a proprietary classifier because "QR Code" is a registered trademark of DENSO WAVE INCORPORATED; keep that trademark notice with QR features.
- The web interfaces self-host DM Sans, Space Grotesk and JetBrains Mono under the SIL Open Font License 1.1.
- `azure-cognitiveservices-speech` is distributed under Microsoft's proprietary SDK license and is used only when a user configures their own Azure credentials.
- `nuitka` (AGPL-3.0-or-later) is a build tool only and must not be redistributed inside release artifacts. Before relying on it for commercial binaries, counsel should confirm the scope of Nuitka's stated permission for compiled programs and the license of the runtime files it embeds.
- `Telethon` is MIT-licensed; the Telegram User transport also includes `pyaes` (MIT), `rsa` (Apache-2.0), and the optional native-AES accelerator `cryptg` (CC0-1.0), whose notices belong in server release bundles.
- `soundfile` Python wheels may include `libsndfile` under LGPL-2.1-or-later; commercial binary bundles need the LGPL notice/source/relinking posture documented in the artifact NOTICE.
- `openwakeword` code is Apache-2.0, but pretrained model assets may be non-commercial. Commercial server builds prune/check `openwakeword/resources/models` assets and must fail if those assets reappear.
- Windows server audio capture uses [PyAudioWPatch 0.2.12.8](https://github.com/s0d3s/PyAudioWPatch) under Apache-2.0 so WASAPI loopback devices are available. Its Windows wheel includes [PortAudio 19.7.0](https://github.com/PortAudio/portaudio/tree/v19.7.0) under MIT; binary notices must retain both license texts. Non-Windows profiles use upstream PyAudio with PortAudio instead.
- `llmfit` (https://github.com/AlexsJones/llmfit) is MIT-licensed and used by the `model_picker_agent` to right-size local models to the host hardware. AutoYou does not vendor it: the prebuilt binary is downloaded from the project's GitHub releases at runtime or bundled into desktop releases, and cached under the AutoYou user-data directory (`tools/llmfit/`). It ships in profiles that include `autoyou_agents`; include its MIT notice in those artifact bundles.
- EmotiVoice's selected inference source is vendored at `vendor/emotivoice/` from `netease-youdao/EmotiVoice` commit `59f0f36de4db12825f4705dd4e0780d79dd6bb01` under Apache-2.0. Python source/bootstrap and full Docker distributions include the inference subset; full voice/connector Windows, macOS, and WSL builds compile its inference modules. The vendored subset excludes upstream demo/training entry points and model checkpoints. Binary-default Windows/macOS profiles omit the voice runtime. Preserve the upstream Apache-2.0 and MIT component notices. The pretrained `syq163/outputs` and `WangZeJun/simbert-base-chinese` checkpoints are not distributed with AutoYou: an administrator must approve and initiate downloads to the configured voice-model directory. Their model cards do not clearly declare a license; review upstream terms before using or redistributing them.
- The optional EmotiVoice English frontend uses NLTK; its tagger and CMUDict data files are downloaded into the managed voice-model directory, not bundled. Review the upstream data-resource terms before redistribution.
- EmotiVoice's inference code includes or is adapted from ESPnet (Apache-2.0), HiFi-GAN (MIT), WeTTS (Apache-2.0), KAN-TTS (MIT), StyleTTS (MIT), `keithito/tacotron` (MIT), NVIDIA Tacotron 2 (BSD-3-Clause), and torch-stft (BSD-3-Clause), and uses PyTorch and Hugging Face Transformers. Their notices are in `vendor/emotivoice/LICENSE` and must accompany the vendored code in every distribution.
- AmplitudeJS 5.3.2 (`https://github.com/521dimensions/amplitudejs`, MIT, Copyright (c) 2021 521 Dimensions) is vendored as the minified file `autoyou_agents/audio_agent/website/frontend/amplitude.min.js`. It is not covered by the AutoYou license. Its MIT notice is `autoyou_agents/audio_agent/website/frontend/LICENSE-amplitudejs.txt` and must accompany the file in every distribution.
- Odysseus (https://github.com/odysseus-dev/odysseus) is AGPL-3.0-or-later. AutoYou Server integrates with its authenticated HTTP companion API, while the optional checkout remains external and gitignored at `vendor/odysseus/`. AutoYou distributions do not bundle the Odysseus source. Any combined image, installer, or hosted distribution must complete a separate AGPL corresponding-source and licensing review.

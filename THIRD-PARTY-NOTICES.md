# AutoYou Third-Party Notices

Last updated: 2026-09-28

This file is the repository-level notice index for AutoYou. It does not replace the exact license texts that must be included with final release artifacts.

## Primary Notice Page

The public attributions page is maintained at:

- `autoyou-website/attributions/index.html`

## Release Artifact Requirement

Every official distribution should include an artifact-specific notice bundle covering only what it ships:

- AutoYou server main package
- AutoYou Server Windows compiled package
- AutoYou Server macOS compiled package
- AutoYou Server Linux/WSL compiled package
- `autoyou_lite`
- Python GUI client
- Android app
- iOS app
- Windows Connect package
- macOS Connect package
- Windows v2 native package
- macOS v2 native package

The notice bundle should include:

- AutoYou Source-Available Personal-Use License.
- Dependency licenses and notices for bundled libraries.
- Apache 2.0 NOTICE obligations where applicable.
- BSD/ISC/MIT notices where applicable.
- LGPLv3 / LGPL-2.1-or-later notices and relinking/reverse-engineering rights where LGPL components are bundled.
- GPLv3 and AGPL-3.0 source/offer notices only for optional separate GPL/AGPL services or research tools that are actually distributed.
- Vendor SDK terms notices for Apple, Google, Microsoft, Azure, GIPHY, WebRTC binaries, and similar platform SDKs.

## Current Known Special Cases

- The offline intent router bundles `sentence-transformers/all-MiniLM-L6-v2` under Apache-2.0 and uses Microsoft's ONNX Runtime under MIT. The pinned model card, attribution, Apache license, runtime license, and runtime third-party notices are in `assets/intent_router/` and accompany the model in mobile, desktop, and server packages. The AutoYou routing profile is separate from the unmodified model weights.
- `signal-cli` is GPLv3 and must remain an optional separate service unless AutoYou is prepared to satisfy GPLv3 obligations for a combined distribution.
- `signal-cli-rest-api` is used as a separate Docker service and has its own notices.
- `wwebjs-api` (`avoylenko/wwebjs-api`, Apache-2.0 / MIT) is an optional Docker container REST API service alternative to the Node.js WhatsApp subprocess (`whatsapp_docker_service.py`). It is used in standalone and compiled server builds (Nuitka binary packages on Windows, macOS, and Linux) to avoid Node native module bundling issues. WhatsApp integration remains owner-only and user-opted.
- `auto-browser` suite (`auto-browser-client`, `auto-browser-langchain`, `auto-browser-mcp` from `https://github.com/LvcidPsyche/auto-browser`, MIT-licensed) is used by `autoyou_agents/browser_agent` to provide controller-backed, real-browser automation and MCP stdio tool execution.
- `cognee` (`https://github.com/topoteretes/cognee`, Apache-2.0) and `instructor` (MIT) provide optional graph and vector memory capabilities in high-resource agent profiles (`requirements/cognee.txt`).
- `OpenSign` (`https://github.com/OpenSignLabs/OpenSign`, AGPL-3.0) and `MikeOSS` (`tools/signtoross/signtoross/apps/mike/`, AGPL-3.0) provide optional legal agreement drafting, local Ollama legal review, and electronic signature workflows within the `SignToROSS` package. They run across separate network/HTTP service boundaries; AutoYou does not relicense or statically link OpenSign/MikeOSS code.
- `tunnelmole` npm client is MIT-licensed, but the self-hosted Tunnelmole Service used by VM2 is AGPL-3.0 unless AutoYou has a separate commercial license. Do not treat the npm client notice as covering the self-hosted service. VM2 release/deploy artifacts need the service license text, corresponding-source/commercial-license evidence, and SBOM/SCA before enterprise production use.
- `undetected-chromedriver` is GPLv3 in research-only requirements and must not be included in official commercial bundles unless approved by counsel.
- `pystray` and `python-telegram-bot` are LGPLv3 and require binary-distribution compliance if bundled.
- `zeroconf` (python-zeroconf) is LGPL-2.1-or-later and ships in server and desktop builds for Lobby discovery (`requirements/base.txt`). Binary bundles need the LGPL notice, a written source offer or the unmodified source, and must leave the module replaceable; release NOTICE files already record it.
- `qrcode` is BSD-3-Clause. Its package metadata also carries a proprietary classifier because "QR Code" is a registered trademark of DENSO WAVE INCORPORATED; keep that trademark notice with QR features.
- The public website self-hosts DM Sans, Space Grotesk and JetBrains Mono under the SIL Open Font License 1.1 (`autoyou-website/assets/fonts/OFL.txt`).
- `azure-cognitiveservices-speech` is distributed under Microsoft's proprietary SDK license and is used only when a user configures their own Azure credentials.
- Android builds can include the Google Maps SDK when `AUTOYOU_MAPS_API_KEY` is supplied; the Google Maps Platform terms require linking the Google Maps Terms of Service and Google Privacy Policy, which the website Terms do.
- `nuitka` (AGPL-3.0-or-later) is a build tool only and must not be redistributed inside release artifacts. Before relying on it for commercial binaries, counsel should confirm the scope of Nuitka's stated permission for compiled programs and the license of the runtime files it embeds.
- `Telethon` is MIT-licensed; the Telegram User transport also includes `pyaes` (MIT), `rsa` (Apache-2.0), and the optional native-AES accelerator `cryptg` (CC0-1.0), whose notices belong in server release bundles.
- `soundfile` Python wheels may include `libsndfile` under LGPL-2.1-or-later; commercial binary bundles need the LGPL notice/source/relinking posture documented in the artifact NOTICE.
- `openwakeword` code is Apache-2.0, but pretrained model assets may be non-commercial. Commercial server builds prune/check `openwakeword/resources/models` assets and must fail if those assets reappear.
- Windows server audio capture uses [PyAudioWPatch 0.2.12.8](https://github.com/s0d3s/PyAudioWPatch) under Apache-2.0 so WASAPI loopback devices are available. Its Windows wheel includes [PortAudio 19.7.0](https://github.com/PortAudio/portaudio/tree/v19.7.0) under MIT; binary notices must retain both license texts. Non-Windows profiles use upstream PyAudio with PortAudio instead.
- `llmfit` (https://github.com/AlexsJones/llmfit) is MIT-licensed and used by the `model_picker_agent` to right-size local models to the host hardware. AutoYou does not vendor it: the prebuilt binary is downloaded from the project's GitHub releases at runtime or bundled into desktop releases, and cached under the AutoYou user-data directory (`tools/llmfit/`). It ships in profiles that include `autoyou_agents`; include its MIT notice in those artifact bundles.
- EmotiVoice's selected inference source is vendored at `vendor/emotivoice/` from `netease-youdao/EmotiVoice` commit `59f0f36de4db12825f4705dd4e0780d79dd6bb01` under Apache-2.0. Python source/bootstrap and full Docker distributions include the inference subset; full voice/connector Windows, macOS, and WSL builds compile its inference modules. The vendored subset excludes upstream demo/training entry points and model checkpoints. Binary-default Windows/macOS profiles omit the voice runtime. Preserve the upstream Apache-2.0 and MIT component notices. The pretrained `syq163/outputs` and `WangZeJun/simbert-base-chinese` checkpoints are not distributed with AutoYou: an administrator must approve and initiate downloads to the configured voice-model directory. Their model cards do not clearly declare a license; review upstream terms before using or redistributing them.
- The optional EmotiVoice English frontend uses NLTK; its tagger and CMUDict data files are downloaded into the managed voice-model directory, not bundled. Review the upstream data-resource terms before redistribution.
- AmplitudeJS 5.3.2 (`https://github.com/521dimensions/amplitudejs`, MIT, Copyright (c) 2021 521 Dimensions) is vendored as the minified file `autoyou_agents/audio_agent/website/frontend/amplitude.min.js`. It is not covered by the AutoYou license. Its MIT notice is `autoyou_agents/audio_agent/website/frontend/LICENSE-amplitudejs.txt` and must accompany the file in every distribution.
- Odysseus (https://github.com/odysseus-dev/odysseus) is AGPL-3.0-or-later. AutoYou Server and Lite integrate with its authenticated HTTP companion API, while the optional checkout remains external and gitignored at `vendor/odysseus/`. AutoYou distributions do not bundle the Odysseus source. Any combined image, installer, or hosted distribution must complete a separate AGPL corresponding-source and licensing review.
- Android release builds copy the generated `android-app` legal bundle into app assets under `legal/`.
- iOS release builds copy the generated `ios-app` legal bundle into app resources under `Legal/`.
- iOS WebRTC (`WebRTC.xcframework`), `AutoYouSodium` (`Clibsodium.xcframework`), `AutoYouPeerLink` (`onnxruntime`), `ExyteChat`, `ExyteMediaPicker`, `Kingfisher`, and vendor SDKs (Google Mobile Ads, UMP) are represented in app acknowledgments before App Store release.
- Windows v2 native application builds use .NET 8 / C#, `Microsoft.WindowsAppSDK` (WinUI 3), `Microsoft.Web.WebView2`, and an IPC pipe bridge to the compiled server engine.
- macOS v2 native application builds use SwiftUI, AppKit, ScreenCaptureKit, and an optional private helper (`AutoYouModel`) for macOS Apple Intelligence Foundation Models.

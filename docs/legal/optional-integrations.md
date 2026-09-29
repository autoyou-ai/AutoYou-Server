# Optional Integration and Runtime Review

Last reviewed: 2026-09-28

This is the public record for AutoYou Server source and optional runtime
profiles. It does not declare an external provider approved for every use case,
and it is not legal advice. An owner enabling an optional service must review
that provider's current terms, pricing, privacy terms, regional availability,
and account requirements for their own use.

## Release boundary

The repository contains source, dependency manifests, tests, and selected build
tooling. Full voice/connector builds compile the selected EmotiVoice inference
modules for Windows, macOS, and WSL; Docker uses the Python source runtime.
No EmotiVoice checkpoints or external provider credentials are bundled.
AutoYou Lite and its PyPI materials are outside this release.

The selected source artifact is documented by the root `LICENSE` and
`THIRD-PARTY-NOTICES.md`, plus
`docs/legal/generated/autoyou-server-source-full/NOTICE.txt` and
`sbom.cdx.json`.

## Messaging

- **WhatsApp:** optional and disabled until owner setup. The current
  [WhatsApp Terms of Service](https://www.whatsapp.com/legal/terms-of-service)
  prohibit bulk messaging, auto-messaging, and unauthorized automated access.
  The source integration is owner-self-chat-only, unaffiliated, and
  experimental; it is not a permission to use WhatsApp contrary to those terms.
- **Signal:** optional and disabled until owner setup. The current
  [Signal Terms of Service](https://signal.org/legal/) prohibit bulk messaging,
  auto-messaging, and unauthorized use. `signal-cli` remains a separate local
  service and is not bundled in this source archive.
- **Telegram:** an owner must use their own API credentials and clearly identify
  use of the Telegram API. The current
  [Telegram API Terms](https://core.telegram.org/api/terms) prohibit using or
  aggregating Telegram data to train, fine-tune, improve, or deploy AI/ML
  models. The source policy limits Telegram User processing to the owner's
  Saved Messages and leaves training export off.

See the [messaging partner policy](messaging-partner-policy.md) for the
implementation boundary.

## Cloud AI and speech providers

- **Google Gemini:** disabled until configured. Under the current
  [Gemini API terms](https://ai.google.dev/gemini-api/terms), do not send
  sensitive, confidential, or personal data to unpaid services; unpaid content
  may be used to improve Google services. Paid-service and regional conditions
  differ and must be selected by the account owner.
- **OpenAI-compatible providers:** LiteLLM and configurable TTS endpoints are
  bring-your-own-provider paths. For OpenAI API use, review the current
  [Services Agreement](https://openai.com/policies/services-agreement/),
  [Service Terms](https://openai.com/policies/service-terms/), and applicable
  privacy/data-processing terms. No OpenAI credential or service entitlement is
  distributed here.
- **Azure Speech:** disabled until configured. The service is governed by the
  account agreement, applicable product terms, and privacy/DPA materials listed
  by [Azure Legal](https://azure.microsoft.com/en-us/support/legal/). Do not
  use preview-only services in production unless the provider terms allow it.
- **ElevenLabs:** disabled until configured. Its current
  [Terms of Service](https://elevenlabs.io/terms-of-use) require appropriate
  rights to input voices and content; account holders must review content-use,
  opt-out, subscription, and privacy choices before enabling it.
- **Picovoice:** disabled until configured and requires its own access
  credential. Its current [Terms of Use](https://picovoice.ai/docs/terms-of-use/)
  impose use, privacy, credential, and licensing conditions. Do not ship its
  models, keys, or software in another artifact without a separate review.

## Runtime downloads and models

- **EmotiVoice:** the inference source is Apache-2.0 and is vendored at a
  pinned upstream commit; voice-enabled Windows, macOS, and WSL builds compile
  only the inference modules they use. The full Docker image includes the
  source runtime. An administrator must confirm before AutoYou downloads
  checkpoints and pronunciation resources into its managed voice-model
  directory. Upstream model cards do not clearly state checkpoint licensing;
  review those terms before downloading or using the weights. The download
  notice in Admin links the destination and licensing caveat. At runtime,
  EmotiVoice selects CUDA through PyTorch when available and otherwise uses
  CPU. AMD ROCm and Apple MLX have not been validated.
- **Cognee:** optional Apache-2.0 memory integration, installed only when the
  relevant profile/build option is selected. This describes a software
  integration and does not imply a formal commercial partnership or
  endorsement.

- **Tunnelmole:** the npm client is MIT-licensed; the hosted service has its
  own terms and is described by its documentation as AGPLv3. Bootstrap can be
  run with `--skip-tunnelmole`, and this is the recommended source-install
  path. Before any binary is bundled or automatically pre-seeded, pin its
  version and checksum and review the current
  [Tunnelmole terms](https://tunnelmole.com/docs/Tunnelmole_Terms_Of_Service.pdf).
- **LLMFit and Whisper runtimes:** source code may obtain a runtime only after
  the owner selects that feature. No executable is part of this archive. A
  future binary release must pin the exact version, verify a checksum, and add
  the runtime to that artifact's SBOM and NOTICE.
- **Models and model hubs:** no model weights are distributed here. Model hubs
  have their own [service terms](https://huggingface.co/terms-of-service), and
  every selected model has its own license, model card, and possible use
  restrictions. Review each model before download, bundling, or redistribution.

## Future binary or container release conditions

Do not treat this source review as clearance for a binary, container image, or
official build. Before distributing one, pin base-image digests and runtime
versions, generate an installed-artifact SBOM and NOTICE, account for the full
npm/browser/native dependency closure, verify model licenses, and obtain the
required signing and provider approvals.

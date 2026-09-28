# Messaging Partner Owner-Only Policy

Last updated: 2026-07-10

This policy documents AutoYou's intended use of Telegram Bot, Telegram User, Signal, and WhatsApp messaging partners. It is an engineering and legal-compliance implementation note, not a formal legal opinion.

## Scope

AutoYou supports messaging partners so the owner of an AutoYou server can pair and talk to their own local AI runtime without depending on AutoYou Cloud Pair.

The intended use is:

- The owner runs the AutoYou server on a computer they control.
- The owner pairs a messaging account or bot they control.
- AutoYou reads and responds only to messages addressed to that paired owner context.
- AutoYou does not provide bulk messaging, marketing messaging, scraping, lead generation, or managed messaging services for third parties.

## Telegram Bot

Telegram Bot uses the Telegram Bot API and supports allow-code and access-gate controls in the admin UI. Operators should approve only Telegram accounts they control or explicitly trust.

## Telegram User

Telegram User is a separate, optional QR-authorized connection to the owner's own Telegram account. It is not a replacement for Telegram Bot and is not a general Telegram client, archive, or automation service.

Telegram User processing is owner-only:

- QR authorization must be completed by the owner from the account they control.
- AutoYou processes only that account's **Saved Messages**. It does not read, log, respond to, search, or export ordinary dialogs, groups, channels, or forwarded content.
- AutoYou does not provide bulk messaging, marketing, scraping, customer support, account management, or third-party automation through Telegram User.
- Telegram controls its own service and data processing. The owner must review and comply with Telegram's current terms and any account or regional requirements.

Telegram User content is not training data by default. A training export is unavailable unless the owner gives explicit, informed, affirmative, continuing consent limited to that owner's Saved Messages; the consent must be recorded and may be withdrawn. That consent never authorizes general-dialog, forwarded-content, or third-party exports.

## Signal

Signal support is optional and uses signal-cli through a separate local Docker service. AutoYou is not affiliated with Signal Messenger LLC.

Signal processing is owner-only:

- QR pairing must be performed by the owner from their Signal account.
- AutoYou processes Signal Notes-to-Self/self-destination messages from that paired account.
- AutoYou does not process ordinary messages received from other Signal users.
- AutoYou does not read, log, or respond to sync messages the owner sent to non-self recipients.

## WhatsApp

WhatsApp support is optional and uses a local WhatsApp Web session controlled by the owner's QR-paired account. AutoYou is not affiliated with WhatsApp LLC or Meta Platforms, Inc.

WhatsApp processing is owner-only:

- QR pairing must be performed by the owner from their own WhatsApp account.
- AutoYou processes verified self-chat messages for the QR-paired account.
- AutoYou ignores non-self chat messages and tracked outbound AutoYou replies.
- AutoYou does not provide WhatsApp bulk messaging, customer support automation, marketing automation, scraping, or account-management services.

## Required User-Facing Notice

Before enabling or pairing Telegram User, Signal, or WhatsApp, AutoYou surfaces a notice that the integration is optional, unaffiliated, and intended only for the owner's self-paired messages. For Telegram User, the notice must also say that only Saved Messages are used, Telegram controls its service and data processing, and training export is off until scoped consent is given. The warning should stay visible in the admin UI, pairing guide, Terms of Use, Privacy Policy, and public attributions page.

## C&D Response Posture

If AutoYou receives a credible cease-and-desist, platform policy notice, store review objection, or account-abuse complaint about Telegram User, Signal, or WhatsApp integration:

1. Preserve the notice and all relevant release/version details.
2. Disable public promotion of the affected integration until reviewed.
3. Confirm whether the complaint concerns user misuse, documentation, or the integration itself.
4. Prefer official platform APIs where available.
5. Be prepared to ship a release that disables the affected integration by default or removes it from hosted/signed distributions while retaining local-source history where legally appropriate.

# AutoYou Guides

These guides are the longer-form companion docs for the main README files.

They were refreshed against the current bootstrap scripts, packaging scripts, pairing flows, and admin endpoints in this repository.

## Start here

| Guide | Best for |
| --- | --- |
| [installation-steps.md](installation-steps.md) | Installing and running the full AutoYou server from source |
| [installation-steps.md](installation-steps.md#8-optional-integrations) | Enabling local EmotiVoice speech and downloading its models |
| [user-journey.md](user-journey.md) | Understanding the end-to-end user flow from setup to daily use |

## Interactive deployment guides

Open the deployed versions at [autoyou.me/guides](https://www.autoyou.me/guides/),
or start the safe local preview server from the repository root and open
`http://127.0.0.1:8000/guides/interactive/`:

```bash
python scripts/serve_guides_preview.py --port 8000
```

| Guide | Best for |
| --- | --- |
| [COMMUNITY_RELAY_SUBMISSION_GUIDE.md](COMMUNITY_RELAY_SUBMISSION_GUIDE.md) | Community Relay enrollment, ports, Docker services, and ready-to-submit checks |
| [HOME_PRIVATE_NETWORK_GUIDE.md](HOME_PRIVATE_NETWORK_GUIDE.md) | Docker, LAN bind, local HTTPS, private STUN/TURN, and app selection |
| [MOBILE_PAIRING_GUIDE.md](MOBILE_PAIRING_GUIDE.md) | iOS and Android pairing modes, security, and connection tests |
| [CHROME_PAIRING_GUIDE.md](CHROME_PAIRING_GUIDE.md) | AutoYou Connect Chrome pairing with a server on another computer |

The local interactive launchers under `guides/interactive/` open the same
versioned HTML source used by the website. The preview server accepts valid
waitlist input only to exercise the UI and returns a local-preview response;
it never forwards an address, sends email, or writes a production database.
Opening an HTML file directly still keeps the form non-submitting because a
`file:` page has no safe local HTTP endpoint.

Guide screenshots are kept at their native proportions and open in the local
gallery viewer. To check or clean metadata recursively for every image
referenced by the guide pages, run:

```bash
python scripts/clean_guide_asset_metadata.py
python scripts/clean_guide_asset_metadata.py --write
```

## Pairing and messaging

| Guide | Best for |
| --- | --- |
| [Messaging partner policy](../docs/legal/messaging-partner-policy.md) | How Telegram, Signal, and WhatsApp are used, including the owner-only Telegram User connection |
| [SIGNAL_QR_PAIRING_GUIDE.md](SIGNAL_QR_PAIRING_GUIDE.md) | Signal-specific setup and QR pairing |
| [WHATSAPP_QR_PAIRING_GUIDE.md](WHATSAPP_QR_PAIRING_GUIDE.md) | WhatsApp-specific setup and QR pairing |

## Build and release

| Guide | Best for |
| --- | --- |
| [WINDOWS_BUILD_GUIDE.md](WINDOWS_BUILD_GUIDE.md) | Running AutoYou on Windows and packaging the full Windows build |
| [MACOS_BUILD_GUIDE.md](MACOS_BUILD_GUIDE.md) | Running AutoYou on macOS and packaging the full macOS build |
| [BUILD_SCRIPT_VALIDATION.md](BUILD_SCRIPT_VALIDATION.md) | Validating bootstrap, packaging, and release automation |

## Architecture

| Guide | Best for |
| --- | --- |
| [SESSION_AND_MEMORY_GUIDE.md](SESSION_AND_MEMORY_GUIDE.md) | How AutoYou keeps conversations continuous and local |
| [Architecture overview](../docs/technical/architecture.md) | How `server.py`, `routers/`, and `core_server/` fit together |

## Related entrypoints

- [root README](../README.md)
- [servers/README.md](../servers/README.md)

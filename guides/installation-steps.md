# Install AutoYou from Source

This guide is for the full AutoYou server that lives at the repository root.

If you only want the lighter mobile bridge, use `autoyou-lite` (distributed separately) instead.

## 1. Decide which profile you want

- `base` installs the smallest full-server setup.
- `local` adds local-model helpers such as Ollama support.
- `recommended` adds messaging integrations without voice or browser-backed internet dependencies.
- `full` adds voice, video, and browser-backed internet tooling for the broadest trial.

## 2. Prerequisites

Required:

- Python 3.10 or newer

Optional, depending on features:

- Ollama for local models
- Node.js 18 or newer for WhatsApp
- Docker Desktop or Docker Engine for Signal

The bootstrap script pre-seeds the Tunnelmole helper for OTP pairing by default, so you do not need Tunnelmole on `PATH` unless you are managing it yourself.

## 3. Full install and start

From the repository root:

Windows:

```bat
.\run_autoyou.bat --profile full
```

macOS or Linux:

```bash
./run_autoyou.sh --profile full
```

## 4. Useful bootstrap variants

Smaller install:

```bash
python scripts/bootstrap_autoyou.py --profile base
python scripts/bootstrap_autoyou.py --profile local
```

Full install:

```bash
python scripts/bootstrap_autoyou.py --profile full
```

Prepare the environment without starting the server:

```bash
python scripts/bootstrap_autoyou.py --profile full --install-only
```

Skip optional preflight steps if you know what you are doing:

```bash
python scripts/bootstrap_autoyou.py --profile full --skip-node --skip-docker --skip-ollama
```

## 5. First launch

The full server starts these local surfaces by default:

- Admin UI: `http://127.0.0.1:8001/`
- AI agent: `http://127.0.0.1:8081/`
- Auth/signaling: `http://127.0.0.1:8002/`

The auth/signaling server is primarily used during pairing and is presented by the server only when setup needs it.

On a fresh setup, the bootstrap path may initialize the server with the default bootstrap password `autoyou123` until you change it in the Admin UI.

## 6. First setup in the Admin UI

Open the Admin UI and:

1. Change the default password if this is a fresh install.
2. Choose your AI backend.
3. Enable only the integrations you plan to use.
4. Pick your pairing and security settings.

## 7. Pair a client

Client applications are distributed separately from this repository. See [www.autoyou.me](https://www.autoyou.me/) for the current apps.

Most people pair through one of these:

- QR pairing
- OTP pairing
- Auto Pair reconnect
- AutoYou Cloud

## 8. Optional integrations

WhatsApp:

- enabled through the Admin UI
- needs Node.js and the `node/whatsapp` dependencies
- guide: [WHATSAPP_QR_PAIRING_GUIDE.md](WHATSAPP_QR_PAIRING_GUIDE.md)

Signal:

- enabled through the Admin UI
- needs Docker
- guide: [SIGNAL_QR_PAIRING_GUIDE.md](SIGNAL_QR_PAIRING_GUIDE.md)

Voice:

- easiest path is the `full` bootstrap profile
- includes the speech-related Python dependencies
- EmotiVoice uses an NVIDIA GPU when the installed PyTorch runtime exposes CUDA; otherwise it falls back to CPU. The current Windows setup was smoke-tested with an RTX 5070 and CUDA-enabled PyTorch.
- AMD ROCm and Apple MLX are not yet validated by this integration.

Telegram User:

- optional owner-only connection to the account's Saved Messages
- policy: [Messaging partner policy](../docs/legal/messaging-partner-policy.md)

## 9. Lightweight alternative

If you want the mobile experience without the full server, bootstrap the lightweight service instead:

```bash
python scripts/bootstrap_autoyou.py --service autoyou-lite --profile recommended
```

That starts the lighter server on:

- `http://127.0.0.1:8099/`

## 10. Troubleshooting

- If port `8001` is already in use, the bootstrap script refuses to start a second AutoYou instance.
- If WhatsApp setup stays disabled, install Node.js 18 or newer and rerun bootstrap without `--skip-node`.
- If Signal stays disabled, start Docker and rerun bootstrap without `--skip-docker`.
- If local models do not respond, start Ollama manually with `ollama serve` or open the Ollama app on macOS.
- If OTP pairing cannot open a tunnel because `tmole` is missing, run `python scripts/download_tunnelmole.py` or set `AUTOYOU_TUNNELMOLE_BIN`.

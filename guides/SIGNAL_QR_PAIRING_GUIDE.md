# Signal QR Pairing Guide

This guide covers the Signal integration used by the full AutoYou server.

## What Signal support is for

Signal support is optional and is designed for the server owner's own paired Signal account.

Current behavior is self-destination oriented:

- link your own Signal account
- use Notes to Self or the paired self-destination path
- use that path for pairing and reconnect messages

## Prerequisites

- Docker is installed and running
- Signal is enabled in AutoYou
- the full AutoYou server is running
- Admin UI is available at `http://127.0.0.1:8001/`

## Start and verify

Check Signal status:

```bash
curl http://127.0.0.1:8001/api/signal/status
```

If Signal is up, the local REST bridge usually responds on port `8082`:

```bash
curl http://127.0.0.1:8082/v1/health
```

## Pair with QR

Request the QR code:

```bash
curl http://127.0.0.1:8001/api/signal/qr
```

Expected response shape:

```json
{
  "success": true,
  "qr_url": "data:image/png;base64,...",
  "device_name": "AutoYou-Signal"
}
```

Then on your primary Signal phone:

1. Open Signal.
2. Go to Linked Devices.
3. Choose the option to link a new device.
4. Scan the QR code from AutoYou.

## Confirm pairing

```bash
curl http://127.0.0.1:8001/api/signal/detailed-status
curl http://127.0.0.1:8001/api/signal/device-name
```

## Use Signal for pairing

After linking, use the paired self-destination path for:

- first-time pairing
- Auto Pair reconnects
- starting a fresh conversation

AutoYou handles the setup details behind the scenes.

## Reset or restart

Clean up the Signal pairing state:

```bash
curl -X POST http://127.0.0.1:8001/api/signal/cleanup
```

Restart the Signal service:

```bash
curl -X POST http://127.0.0.1:8001/api/signal/restart
```

## Troubleshooting

- If Signal reports `service not available`, verify Docker is running and the Signal feature is enabled.
- If QR generation says the device is already paired, run the cleanup endpoint before requesting a new QR code.
- Signal pairing QR codes are cached for 50 seconds to ensure a stable linking session while scanning. If a new QR code is needed, use `curl http://127.0.0.1:8001/api/signal/qr?refresh=true` or click "Refresh QR code" in the Admin UI dialog.
- If messages appear in Signal but AutoYou does not react, confirm you are using the paired owner/self-destination path rather than an unrelated inbound chat.
- If the local REST bridge does not respond on port `8082`, inspect the Signal container state before retrying pairing.

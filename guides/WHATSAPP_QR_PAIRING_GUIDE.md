# WhatsApp QR Pairing Guide

This guide covers the WhatsApp integration used by the full AutoYou server.

## What WhatsApp support is for

WhatsApp support is optional and is designed for the server owner's own QR-paired WhatsApp account.

Current behavior is self-chat oriented:

- link your own WhatsApp account
- use your self-chat for commands
- let AutoYou treat that self-chat as the approved pairing and message path

## Prerequisites

- Node.js 18 or newer
- `npm install` completed in `node/whatsapp`
- WhatsApp enabled in AutoYou
- the full AutoYou server is running
- Admin UI available at `http://127.0.0.1:8001/`

The bootstrap script handles the Node dependency install unless you skip it.

## Start and verify

Check WhatsApp status:

```bash
curl http://127.0.0.1:8001/api/whatsapp/status
```

## Pair with QR

Request the QR code:

```bash
curl http://127.0.0.1:8001/api/whatsapp/qr
```

Expected response shape:

```json
{
  "success": true,
  "qr_url": "data:image/png;base64,...",
  "device_name": "AutoYou-WhatsApp"
}
```

Then on your phone:

1. Open WhatsApp.
2. Open Linked Devices.
3. Choose the option to link a device.
4. Scan the QR code from AutoYou.

## Confirm pairing

```bash
curl http://127.0.0.1:8001/api/whatsapp/status
```

The status response tracks whether the Node process is running, whether the transport is healthy, and whether the phone number is known.

## Use WhatsApp for pairing

After linking, use your self-chat for:

- first-time pairing
- Auto Pair reconnects
- starting a fresh conversation

AutoYou handles the setup details behind the scenes.

## Reset or restart

Reset the WhatsApp session:

```bash
curl -X POST http://127.0.0.1:8001/api/whatsapp/reset
```

Restart the WhatsApp service:

```bash
curl -X POST http://127.0.0.1:8001/api/whatsapp/restart
```

## Troubleshooting

- If WhatsApp says the service is unavailable, install Node.js and rerun bootstrap without `--skip-node`.
- If QR generation says the device is already paired, reset the session before requesting a new QR code.
- If the Node process keeps restarting, inspect the WhatsApp service logs and confirm port `8083` is free.
- If self-chat messages do not trigger pairing, confirm you are using the QR-paired owner account and not another chat thread.

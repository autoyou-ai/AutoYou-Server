# AutoYou User Journey

This guide describes the practical end-to-end AutoYou experience as it exists in this repository today.

## 1. Pick your setup

You usually start with one of two paths:

- the full AutoYou server from the repository root
- the lighter `autoyou-lite` bridge from `openclaw/`

Use the full server if you want the broader admin experience, agents, and built-in messaging integrations.

Use `autoyou-lite` if you mainly want to pair the AutoYou mobile app with your own AI backend and keep the install smaller.

## 2. Start the service

For the full server, the usual starting point is:

Windows:

```bat
.\run_autoyou.bat --profile full
```

macOS or Linux:

```bash
./run_autoyou.sh --profile full
```

For the lightweight path:

```bash
python scripts/bootstrap_autoyou.py --service autoyou-lite --profile recommended
```

## 3. Open the local admin page

Full server:

- `http://127.0.0.1:8001/`

Lightweight server:

- `http://127.0.0.1:8099/`

From there you configure:

- password and security mode
- AI backend
- messaging partners
- voice features
- pairing preferences

## 4. Pair a device

AutoYou supports several pairing styles:

- QR pairing
- OTP pairing
- Auto Pair reconnect
- AutoYou Cloud

Messaging partner behavior depends on the integration:

- Telegram follows the bot and access-control model
- Telegram User connects the owner's account through Saved Messages only
- Signal expects the owner's self-destination flow
- WhatsApp expects the owner's self-chat flow

## 5. Use the client

Once paired, the user experience centers around the secure app connection.

Common actions:

- chat with the configured AI backend
- browse local web tools through the browser bridge
- send or receive images, voice notes, and other media
- use voice and video features if the server has them enabled

## 6. Reconnect later

After the first successful pairing, later reconnects are usually easier because clients can use Auto Pair or cloud-assisted flows instead of repeating the full first-time setup.

That is why the common lifecycle becomes:

1. install and configure once
2. pair once
3. reconnect quickly after that

## 7. Daily use patterns

Typical real-world patterns are:

- use the phone app for chat, quick actions, and remote browser access
- use the desktop client when you want a local desktop shell
- use messaging integrations when you want low-friction command entry from the tools you already use

## 8. When to switch setups

Move from `autoyou-lite` to the full server when you want:

- runtime-installable agents
- the fuller admin surface
- built-in local service integrations
- more of the AutoYou platform in one process

Move from the full server to `autoyou-lite` when you want:

- a smaller install
- a simpler mobile bridge
- your own separate AI backend without the rest of the full server stack

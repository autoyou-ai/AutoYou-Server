# Mobile pairing guide for iOS and Android

AutoYou Connect is the companion mobile application. The AutoYou server stays
on the computer you control. Choose the pairing mode that matches your network
and security needs, then keep the same server password and security mode on
both sides.

## Before pairing

1. Start AutoYou on the computer.
2. Replace the bootstrap password and choose the security mode in the Admin
   Overview.
3. Confirm the server is on the same Wi-Fi for Local Pair, or link the server
   to AutoYou Cloud for remote pairing.
4. Install AutoYou Connect from [Google Play](https://play.google.com/store/apps/details?id=com.autoyou.app)
   or the [App Store](https://apps.apple.com/us/app/autoyou/id6760363728).
5. Open the app's Connection tab.

## Pick a mode

| Mode | Best for | Needs |
| --- | --- | --- |
| Local Pair | Same Wi-Fi or Ethernet | LAN bind and server address |
| Auto Pair | Guided setup with nearby or known server | AutoYou pairing flow |
| Telegram Bot | Pairing through your own bot | Configured Telegram Bot and approved sender |
| Telegram QR User | Your own Telegram Saved Messages | Your own Telegram account and QR flow |
| Signal QR | Signal-based private pairing | Signal service and QR approval |
| Bluetooth Pair | Nearby device discovery | Bluetooth enabled on both devices |
| OTP Pair | A short-lived guided code | Server security mode and an active code |

Do not choose a mode because it looks more remote. Local Pair is usually the
smallest trust surface on a home network. Cloud and messaging modes add
convenience but should be protected by the server password, approved senders,
and the selected security mode.

## Local Pair

Use this when the phone and computer are on the same trusted network.

1. In Admin Overview, set **home network access** and restart AutoYou. The
   process must bind to `0.0.0.0`; the change is not live until restart.
2. In the admin Local Pair panel, copy the LAN address and port.
3. In AutoYou Connect, choose **Local Pair - same Wi-Fi / network**.
4. Enter the address, port, and server password. If HTTPS is enabled, use the
   HTTPS URL and install the server CA first.
5. Confirm the connection, then test Chat and Browser.

The LAN bind exposes several local server surfaces. Keep it on a trusted
network and do not forward the admin ports to the internet.

## Auto Pair

Auto Pair is the guided route for a server and client that can reach the same
pairing surface. Open Auto Pair in the Connection tab, follow the QR or code
prompt shown by the server, and approve the request on the computer. If the
code is stale, cancel the attempt and generate a new one instead of reusing an
old screenshot.

## Telegram Bot

Configure a Telegram Bot in the server Admin UI, then restrict approved sender
IDs or usernames. From the phone, select the Telegram Bot pairing option and
scan or follow the generated pairing instruction. Approve only a request you
started. The bot should not be treated as a general public pairing inbox.

## Telegram QR User pairing

Telegram User pairing uses a Telegram account you own and the account's Saved
Messages surface. Connect that account in the server's Telegram User settings,
complete the QR login on the computer, and keep the account limited to Saved
Messages. Then use the mobile Connection tab to complete the displayed pairing
flow. Never enter a Telegram login code into an untrusted chat or share a
session export.

## Signal QR pairing

Start the repository-native Signal service, open Signal pairing in the server,
and scan the QR from the Signal-linked device. Approve the exact device and
wait for the server to report a healthy pairing before closing the setup
screen. If the QR expires, generate a fresh one. The Signal QR flow is separate
from Local Pair and does not make a public TURN endpoint.

## Bluetooth Pair

Turn on Bluetooth on both devices, keep them nearby, and select **Bluetooth
Pair** in AutoYou Connect. Approve the matching device name on the computer and
phone. If the device is not found, check operating-system Bluetooth permission,
remove an old stale pairing, and retry with both apps open.

Bluetooth discovery is only a transport for the pairing handshake. It does not
replace the server password or the selected security mode.

## OTP Pair

Use OTP Pair when the server presents a short-lived code. Enter the code only
in the AutoYou Connect app that you opened yourself, confirm the server name,
and finish before the code expires. Never post the code in a group chat or use
an OTP captured from an old screen.

## Security modes

- **Normal**: simplest local setup. Use only on a trusted device and network.
- **Secure**: recommended baseline for routine use.
- **Secure Professional**: stronger protected pairing and authenticator options
  for shared or remote workflows.
- **Secure Professional Maximus**: strongest local protection for saved
  sessions, agent data, websites, notes, and settings on the computer.

If a pairing method asks for the server password, enter it through the app's
protected field. Do not place it in a URL, QR screenshot, browser bookmark, or
support ticket.

## Test the complete connection

After pairing, verify in order:

1. Connection status shows the expected server name.
2. A simple Chat message returns.
3. Browser opens through the server.
4. A voice or video call can be started if enabled.
5. Reopening the app does not silently switch to a different server.

If Local Pair works but remote access does not, the pairing is healthy and the
network path is the issue. Check AutoYou Cloud, public-link status, connection
helpers, and firewall rules separately.

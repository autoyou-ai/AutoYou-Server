---
title: Pairing
description: Public overview of AutoYou pairing.
---

# Pairing

AutoYou supports local pairing, message-assisted pairing, QR setup, and Cloud Pair. Each path is designed to connect only approved devices to your own server.

Use the setup guide for the recommended pairing path for your device.

## Pairing and Peer Link are different things

Pairing connects an app to a **computer** (this server). Peer Link connects an
app to **another app**. Both end in the same kind of encrypted link, which is
why they look alike, but what is at the other end differs.

| | Pairing (Local, Cloud, AutoPair, OTP, Bluetooth) | Peer Link |
| --- | --- | --- |
| Other end | An AutoYou computer | Another AutoYou app (phone or desktop) |
| What proves you may connect | The computer's pairing password, plus its security mode and authenticator code when set | A Peer Authenticator kept by the apps, or a contact code exchanged through AutoYou Cloud. Never the computer's password |
| Who answers chat | AutoYou AI on the computer | The other person, or their "My AI replies" assistant |
| A call | You talk to AutoYou AI; the computer's microphone and sound can be shared with you | You talk to the other person |
| Websites, agents, remote desktop | Yes, by the computer's access role | Only if the other app shares its computer with you, and never remote desktop |
| Approval | The password is the approval | The receiving app approves each connection |

Peer Link joins two apps. The app that receives the connection is the host. If
the host is itself connected to its computer and allows it, the guest's chat
and browsing are passed on to that computer; that is the only way a Peer Link
reaches a server, and it goes one step, not through a chain of apps. Group
conversations are Lobbies, which are separate from both.

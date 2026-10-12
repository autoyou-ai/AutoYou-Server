---
title: Security Modes
description: What each security mode protects and how to choose one
---

# Security Modes

AutoYou has four security modes. The mode controls how device-pairing messages are protected, and the highest mode also protects data stored on your computer. One mode applies to the whole server, so every device pairs under the same rules.

Change it in the admin console under **Security**. For the implementation details, see [Security & Authentication Architecture](../architecture/security-and-auth.mdx).

## Quick comparison

| Mode | Pairing messages | What a device needs to pair | Data on this computer |
| --- | --- | --- | --- |
| **Normal** | Not encrypted | A one-time code | Unchanged |
| **Secure** | Encrypted with the server password | The server password | Unchanged |
| **Secure Professional** | Encrypted with the server password | The server password and a current authenticator code | Unchanged |
| **Secure Professional Maximus** | Same as Secure Professional | The server password and a current authenticator code | Saved sessions, agent data, websites, notes, and settings are encrypted |

A new installation starts in **Secure** mode.

## Normal

Pairing uses a one-time code, and the pairing messages themselves are not encrypted. Anyone who can observe them can read them. Use it only on a network you trust and only for older local-first clients that need it.

## Secure

Pairing messages are encrypted with the server password. A device proves it knows the password without sending it, so someone who records the exchange cannot use the recording to test password guesses. This is a good default for a home or office network.

## Secure Professional

Secure Professional adds a second factor. The device also supplies the current six-digit code from the shared authenticator. Use it when the server can be reached from networks you do not control.

AutoYou keeps **one** shared authenticator setup. The same setup serves Secure Professional pairing, the authenticator pair-code mode, and elevation for the admin agent.

### Setting up the authenticator

1. Open **Security** in the admin console.
2. Create or import the authenticator. A QR code and a setup key are shown.
3. Add it to an authenticator app, such as Google Authenticator, 1Password, or Microsoft Authenticator, by scanning the QR code or typing the setup key.
4. Confirm with the current six-digit code.

The **setup key** is the long value you add to the authenticator app once. The **pairing code** is the six-digit number the app shows, which changes every 30 seconds. If a client has separate fields for them, put the setup key in the setup-key field and the current six-digit number in the code field.

AutoYou does not generate or store recovery codes. Keep the authenticator device safe, and treat the setup key like a password.

## Secure Professional Maximus

Maximus keeps the same password-and-authenticator pairing protection and also encrypts AutoYou-managed data on the computer: saved sessions, agent data, websites, notes, settings, and the databases behind them. The key is held in the operating system's credential store.

It does not protect files outside AutoYou-managed storage, and it does not protect data you send to a cloud model provider. To replace the storage key, an admin session can rotate it (`POST /api/admin/security/storage/rotate`). A password change does not by itself replace the storage key.

## Pairing security tier

A separate setting controls how much work a chat-message pairing takes:

- **Quick Pairing** lets a client pair with one chat message and has the best compatibility.
- **Enhanced Pairing** requires an extra round trip for every client, which makes password guessing harder.

## Changing modes

Select a different mode under **Security** and save it. Moving up to Secure Professional needs an authenticator set up first. Moving down removes the protection the higher mode provided, so do it only on a network you trust.

Credentials and security settings can be changed only from the computer itself or from an HTTPS admin session opened directly on the admin port. A paired device's browser can read the console but cannot change them.

## If you are locked out

- **Forgot the server password, but you can still sign in:** change it under **Security** in the admin console.
- **Forgot the server password and cannot sign in:** there is no recovery code. On the computer running the server, the sign-in page offers a local reset that erases AutoYou's local data and shuts the server down after you type `RESET`. Restore from your own backup afterward if you have one.
- **Lost the authenticator device:** set up a new authenticator from an admin session on the computer itself, and update the code source on each device that pairs.

## Choosing a mode

| Situation | Suggested mode |
| --- | --- |
| Home network, only your own devices | Secure |
| Shared or guest network | Secure |
| The server is reachable from the internet, or you travel with sensitive data | Secure Professional or Maximus |
| Shared computer or travel laptop where saved data should not stay readable on disk | Maximus |
| Older client that cannot do secure pairing, on a network you trust | Normal |

## Good habits

- Use a strong, unique server password. The admin console can generate one.
- Keep the operating system and AutoYou up to date, and use full-disk encryption on laptops that travel.
- Do not share the server password, the authenticator setup key, or codes.
- Keep backups. A reset or a lost key can make encrypted data unrecoverable.

## Next steps

- [Encryption](encryption.md)
- [Data privacy](data-privacy.md)

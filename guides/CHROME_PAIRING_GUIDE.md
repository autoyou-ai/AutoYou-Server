# Pair AutoYou Connect in Chrome with another computer

Use this guide when AutoYou is running on one computer and you want to use
AutoYou Connect from Chrome on another computer. The Chrome deliverable is a
Manifest V3 browser extension. It is a client, not a second AutoYou server.

The server keeps the AI runtime, browser routes, agent websites, and pairing
policy. Chrome supplies the chat, call, browser, and remote desktop surface
after the secure connection is established.

## The short version

1. Start AutoYou on the server computer and verify its server name and pairing
   password.
2. Make sure the server computer is reachable from the Chrome computer. For a
   same-LAN connection, use a LAN address and a configured server port. Do not
   expose the admin surface to the public internet just to make Chrome work.
3. Install [AutoYou Connect from the Chrome Web Store](https://chromewebstore.google.com/detail/autoyou-connect/ehjfehgpgmfmakeliojpdddgoddiebdg).
4. Open the extension, choose a pairing mode, and enter the server password in
   Settings. For Local Pair, enter the server computer's host or IP and port.
5. Match the server security mode and security tier. Use the authenticator
   setup key only for Secure Professional flows.
6. Click Connect, approve the request on the intended server if prompted, then
   test Chat and Browser before using calls or remote desktop.

The website download selector links Chrome users to the native store. It does
not expose Android APK files or Python wheel files as website downloads.

## Choose the right Chrome installation

### Normal use: Chrome Web Store

Install the signed store listing:

[Install AutoYou Connect for Chrome](https://chromewebstore.google.com/detail/autoyou-connect/ehjfehgpgmfmakeliojpdddgoddiebdg)

After installation, pin AutoYou Connect to the Chrome toolbar and open its
Settings page once. The extension stores each pairing mode in a separate local
profile, so a Local Pair profile does not overwrite an OTP or Cloud profile.

### Local development: load the extension from source

This path is for maintainers and testers. End users should use the store
listing.

From the repository root:

```text
cd clients/chrome
npm install
npm test
node tools/package.mjs
```

The package command produces a versioned archive under `clients/chrome/dist/`.
For an unpacked test, open `chrome://extensions`, enable Developer mode,
choose Load unpacked, and select the `clients/chrome` extension directory.
Reload the extension after source changes. Do not upload a development
directory or an archive containing source-only test material to the Chrome Web
Store.

The supported extension version is recorded in
`clients/chrome/manifest.json`. The package and tests are the source of truth
for the browser client, not a copied website download.

## Prepare the computer running AutoYou

Complete the normal server setup first. This guide assumes that the server is
already installed and that its admin page can be opened on the server
computer.

### Same home or office network

For Local Pair, the Chrome computer must reach the server computer directly.

1. In the server admin UI, enable trusted home or LAN access, or configure the
   server bind address as `0.0.0.0`.
2. Restart AutoYou after changing the bind address.
3. Find the server computer's current LAN address, for example
   `192.168.1.20`. Do not reuse a stale address from a previous router lease.
4. Allow the configured AutoYou HTTP port through the server computer's private
   firewall. The current Local Pair default is port `8001`; use the actual port
   shown by the server if it was changed. AutoYou Lite commonly uses `8099`.
5. Keep the network profile private. A LAN bind is not a public hosting
   permission.

Check reachability from the Chrome computer by opening the server address in a
new tab, for example `http://192.168.1.20:8001/login`. A login page or a
server response proves that TCP routing works. A browser error page means the
firewall, address, port, bind address, or network isolation still needs to be
fixed.

### A different network

Do not forward the admin port from the home router as a first resort. Use one
of the supported remote paths instead:

- Cloud Pair, when the account and plan allow it.
- OTP Pair through the configured public link or tunnel.
- Auto Pair through an approved Telegram, Signal, or WhatsApp route.
- A properly managed VPN that places both computers on a private network.

If you deliberately operate a public reverse proxy, use HTTPS, a stable
hostname, authentication, and a separate policy for the admin surface. A
Cloudflare Tunnel or ordinary HTTPS tunnel is a web access path. It is not a
replacement for a direct UDP TURN endpoint.

## Install and configure the extension

1. Open the extension from the Chrome toolbar.
2. Open Settings before trying to connect.
3. Select the pairing profile that matches the route you prepared.
4. Enter the server password. It is stored locally in the extension's protected
   browser storage and is sent only to the server selected by the active
   profile.
5. Select the same security mode as the server. Secure is the normal baseline.
   Secure Professional requires the configured 2FA setup key or current code.
6. Select the security tier. Quick Pairing is convenient for a one-message
   handoff. Enhanced Pairing performs an additional authenticated handshake and
   is preferable when the route is shared or remote.
7. Give the browser a useful client name, such as `Chrome office computer`.
8. Leave connection helpers empty to use defaults, or enter the exact STUN or
   TURN helpers supplied by the server administrator.

The extension keeps the password, security mode, tier, authenticator secret,
and connection helpers per pairing mode. Check the active mode before editing
or deleting a saved profile.

## Local Pair: Chrome to a server on another computer

Local Pair is the direct path for a trusted LAN or a private VPN. It does not
use AutoYou Cloud, a messaging app, or copy and paste.

### In the Chrome extension

1. Select `Local Pair`.
2. Enter the server computer's LAN hostname or IP address, not the Chrome
   computer's address.
3. Enter the server HTTP port, usually `8001` for the full server or the port
   configured for AutoYou Lite.
4. Save the profile and click Connect.
5. Chrome may ask for optional access to the exact HTTP origin. Grant the
   origin you entered, such as `http://192.168.1.20:8001`, if you want Local
   Pair to continue.

The extension opens a short-lived background helper tab on the server origin.
This is intentional. Current Chrome local-network protections do not allow an
extension page to make the full login and pairing exchange directly to a
private address. The helper tab makes same-origin requests to `/login`, then
`/api/autopair_hello` for Enhanced Pairing, and finally `/api/autopair`. It is
closed after the exchange.

The helper tab does not mean the server has been embedded into an untrusted
page. The extension checks that the tab reached the requested HTTP origin and
uses the server's first-party session cookie for the exchange.

### On the server computer

The server may show a new client or approval prompt. Confirm the server name,
client name, and time before approving. If the server is in Secure
Professional mode, complete the requested authenticator step on the client.

When the secure WebRTC channel opens, the extension should show the server
name and a connected state. The helper tab can disappear without affecting the
active connection.

## Auto Pair: copy and paste through an approved message path

Use Auto Pair when the computers cannot reach each other directly and you have
an approved messaging path.

1. Select `Auto Pair` in the extension.
2. Choose the configured Telegram, Signal, or WhatsApp recipient.
3. Click Generate or Connect. Copy the complete message produced by the
   extension.
4. Send it to your own AutoYou server through the approved private route.
5. Paste the complete server reply into the extension, or use the optional
   clipboard check if you explicitly enabled it.
6. For Enhanced Pairing, complete the hello response before sending the offer.
7. Approve the final pairing only when the server identity is correct.

Auto Pair has no direct HTTP request to the remote server. It is a human
relayed exchange. A stale or incomplete response must be discarded and a new
exchange generated. Never post an offer, answer, password, QR image, or 2FA
setup key in a public channel.

## OTP Pair: use a short-lived remote code

OTP Pair is useful when the server exposes an approved public link or tunnel.

1. Select `OTP Pair`.
2. Click the command button in the extension and send the generated command to
   the server through the approved route.
3. Paste the complete `/otp` response into the response box.
4. Click Connect and let the extension authenticate to the returned public
   link.
5. Confirm the server name and discard the response after use.

Secure Professional may use a two-step pairing hello or may be configured to
use the current authenticator code as the pairing code. The server and Chrome
profile must use the same setting. An OTP response is not a permanent server
URL and should not be bookmarked or reused.

## Cloud Pair

Cloud Pair is the account-backed path for a client outside the LAN. Sign in
from the extension, confirm that the account and plan permit Cloud Pair, then
start the connection from the Cloud profile. Shared TURN relays can help a
connection that needs relay assistance, but a relay failure does not replace
the server's signaling or authorization checks.

If the extension says that an active subscription is required, the pairing
path is working as designed. Use Local Pair, VPN, OTP, or Auto Pair instead of
trying to bypass that account requirement.

## What works after pairing

The first test should be intentionally small:

1. Send a short Chat message and verify that the server replies.
2. Open the Browser view and choose a server-published website.
3. Confirm that the browser home points to the expected AutoYou route.
4. If calls are enabled, test audio before video and remote desktop.
5. If you open Browser Control or Remote Desktop, approve the Chrome debugger
   notice. Chrome shows that notice because the feature uses the debugger API
   for the active tab. Close the feature when finished.

The extension uses the local browser bridge at `http://autoyou.localhost:<port>`
for server-published browser routes after the secure connection is active. A
bridge page or a helper tab is not the AutoYou server itself. It is the local
browser surface for a connection that has already been paired.

## Troubleshooting by symptom

| Symptom | Likely cause | Correction |
| --- | --- | --- |
| Could not open helper page | Wrong host, port, bind address, firewall, or LAN isolation | Open the exact server URL in Chrome and correct the network path first. |
| HTTP 401 during Local Pair | Wrong password or mismatched security mode | Re-enter the server password and match the server security mode. |
| HTTP 403 or origin error | Chrome origin permission or server origin policy | Grant the exact origin requested by the extension and avoid mixed HTTP and HTTPS addresses. |
| Login page opens but pairing does not finish | Stale session or expired pairing state | Close the old helper tab, restart the connection, and generate a fresh exchange. |
| WebRTC channel times out | Missing or invalid STUN/TURN helper, blocked UDP, or wrong LAN route | Start with the server's default helpers, then add the exact TURN details if the server provides them. |
| Local Pair works on the server computer but not from Chrome computer | Server is still bound to loopback or the router isolates clients | Bind to the trusted LAN, allow the private firewall port, and verify both devices are on the same network. |
| Browser view is blank | The secure channel is not active or no route is published | Test Chat first, refresh server browser status, and select a published route. |
| Chrome shows a debugger banner | Browser Control or Remote Desktop is active | This is expected for that feature. Close the feature when finished. |
| Certificate warning on a private HTTPS page | The local certificate is self-signed or not trusted on this computer | Install the server's private CA on the client, or use a trusted certificate. Do not weaken Chrome security for a public endpoint. |
| Auto Pair reply is rejected | Incomplete, expired, or wrong-mode response | Generate a fresh response and paste the complete text for the active mode. |
| Cloud Pair says the plan is not eligible | Account or subscription policy | Use a supported plan or choose Local Pair, VPN, OTP, or Auto Pair. |

## Ready-to-connect checklist

- [ ] AutoYou is running on the intended server computer.
- [ ] The server name and password were confirmed on that computer.
- [ ] The selected route is private and intentional.
- [ ] The server bind address and firewall allow the Chrome computer when using
      Local Pair.
- [ ] The current host and port were entered in the Chrome Local Pair profile.
- [ ] The Chrome extension was installed from the official store or loaded
      from a deliberate local development checkout.
- [ ] Security mode and tier match on both sides.
- [ ] Chat was tested before calls, Browser Control, or Remote Desktop.
- [ ] No password, OTP, QR image, authenticator secret, or SDP was published.

## Server reference endpoints

- WebRTC pairing endpoint: `/api/pair/webrtc`
- Local pairing discovery: `/api/connect/discover`
- Local token validation: `/api/connect/verify`


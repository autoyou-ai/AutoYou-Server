# How to host AutoYou in your home or private network

This is the local-first path for a trusted home or office LAN. It is not a
public hosting recipe. The safe default is loopback-only access; widen the bind
only when a phone, another computer, or a local browser must reach the server.

## Choose the deployment shape

- **Docker Desktop on Windows or macOS**: easiest for the container runtime and
  Compose.
- **Docker Engine and Compose on Linux**: best for an always-on home server or
  a small private VPS.
- **AutoYou desktop or WSL package**: use the packaged app when you do not need
  a containerized server.

Install Docker Desktop or Docker Engine with Compose v2 before using the
AutoYou Docker image. Keep the data volumes on a backed-up local disk.

## Start the private server

Use the versioned Docker image from the private registry instructions. The
exact image name and tag should match the release you have selected. Do not
copy a community relay token into a private server compose file.

For a LAN server, set the server bind to all local interfaces and restart the
process:

```dotenv
AUTOYOU_BIND_HOST=0.0.0.0
```

The equivalent packaged command is:

```bash
AutoYou --host 0.0.0.0
```

The admin UI, Auth Server, and Websites & Browser surfaces become reachable by
devices on the same network. The AI Agent server stays loopback-only unless
its separate LAN access setting is explicitly enabled. Use a trusted Wi-Fi or
Ethernet network, replace the bootstrap password, and do not forward the admin
ports from the router to the public internet.

## Local HTTPS and browser warnings

AutoYou can run its opt-in local HTTPS listener on port `8443`. Enable HTTPS in
the Admin Overview or the saved `server.https_enabled` setting, then restart.
The server exposes its local CA at `/ca.crt`.

1. Open the server's HTTPS URL from the same network.
2. Download `/ca.crt` from that server.
3. Install and trust it on each phone or computer that will connect.
4. Reopen AutoYou Connect after the trust change.

A private certificate is not trusted automatically. Chrome, Safari, Firefox,
iOS, and Android may show a warning until the CA is installed. Do not teach
users to click through an insecure warning on an internet-facing address. For
public access, use AutoYou Cloud or a real certificate and a properly secured
reverse proxy.

If the browser still warns after installing the CA, check that the URL uses the
hostname or LAN address covered by the certificate, that the device clock is
correct, and that the browser was fully restarted. A self-signed leaf
certificate is not the same as a trusted local CA.

## Configure the connection helper

On the server, open **Connectivity** and review the Local Pair panel. On each
phone, computer, Chrome extension, AutoYou Connect client, or AutoYou Lite
client, use the Connection settings and enter the server address shown by the
admin shell. Use the HTTPS URL when the device trusts the local CA.

Local Pair is direct LAN traffic. Auto Pair and Cloud Pair are different paths:
they need the cloud account and the selected pairing security mode. A local
server does not need a Community Relay to pair devices on the same network.

## Private STUN/TURN for a home network

Use [`openstorey/local-stunturn:8.0.8.0`](https://hub.docker.com/r/openstorey/local-stunturn) for a private sandbox or Local Pair
scenario when direct host candidates are not enough. The private service is not
the public Community Relay roster and should not be advertised as one.

If you need remote WebRTC from outside the home, choose one of these explicit
paths:

- AutoYou Cloud or a supported public link for managed remote access.
- A public VPS running the Community Relay submission with its required ports.
- A carefully configured home router with public IPv4, forwarding, firewall
  rules, and stable DNS.

An ordinary Cloudflare Tunnel is an HTTP/TCP tunnel and is not a replacement for
the public UDP TURN relay range. Keep TURN and STUN DNS records DNS-only unless
you are using a tested Layer 4 service that supports the complete port contract.

## Supported applications

The website download selector is the safe source for current artifacts. It
does not expose Android APK files or Python wheel files as website downloads.

| Platform | Application | Install path |
| --- | --- | --- |
| Linux / WSL | AutoYou server | Account-gated WSL or Ubuntu artifact |
| Linux / WSL | AutoYou Connect | Account-gated client artifact |
| Linux / WSL | AutoYou Lite | PyPI package or supported build path |
| Windows | AutoYou | Microsoft Store |
| Windows | AutoYou Connect | Microsoft Store |
| Windows | AutoYou Lite | Inno Setup installer in the account-gated files area |
| macOS | AutoYou | Notarized DMG in the account-gated files area |
| macOS | AutoYou Connect | Notarized DMG when the store path is available |
| macOS | AutoYou Lite | Notarized DMG when published |
| Android | AutoYou Connect | Google Play |
| iOS | AutoYou Connect | Apple App Store |
| Python | AutoYou Lite | PyPI project page only; no WHL download here |
| Chrome | AutoYou Connect | Chrome Web Store |
| Docker | AutoYou | Docker Hub or account-gated archive |
| Docker | Private relay | Docker Hub and the account-gated relay artifact |
| Docker | Community relay | Docker Hub and the account-gated relay artifacts |

## Backups and recovery

Back up the AutoYou configuration and Docker volumes before changing bind or
TLS settings. Keep the private CA and server password protected. If a client
stops connecting, first verify the server is running, the LAN address has not
changed, the firewall allows the chosen ports, and the client is using the
current connection helper.

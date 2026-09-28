# Community Relay submission

## Ready to submit

This guide is for a public Community Relay donor. A donor runs two services:

- `community-relay-coturn` is the TURN media plane.
- `community-relay-agent` enrolls the donor, sends heartbeats, and reports usage.

They are separate because the media plane should stay small and hardened while
the agent owns the control-plane enrollment and health contract. Run both on
the same Linux host.

Published images:

- [community-relay-coturn](https://hub.docker.com/r/openstorey/community-relay-coturn)
- [community-relay-agent](https://hub.docker.com/r/openstorey/community-relay-agent)

### Recommended setup

Use a Linux VPS with a static public IPv4 address and a DNS hostname. This is
usually simpler and more reliable than a home router. A home network can work,
but it must have a public IPv4 address, no CGNAT, explicit router forwarding,
firewall rules, stable DNS, and a continuously running Docker host.

### Dashboard submission checklist

In the AutoYou dashboard, open **Connection**, choose **Add Relay**, and enter:

1. Public hostname or IP.
2. UDP port `3478`.
3. TCP port `3478`.
4. Optional TLS TCP port `5349`.
5. UDP relay range `49152-49200`.
6. Declared bandwidth and monthly capacity.
7. Relay path `public-ip`.
8. Optional HTTPS health URL, if you operate one separately.

Copy the one-shot enrollment token only after the form is complete. It is
consumed once and must not be committed, pasted into a public issue, or sent to
the website.

### Donor machine

Copy `autoyou-core/autoyou-distributed/.env.example` to `.env` and set the
images and token:

```dotenv
AUTOYOU_COMMUNITY_RELAY_COTURN_IMAGE=openstorey/community-relay-coturn:8.0.8.0
AUTOYOU_COMMUNITY_RELAY_AGENT_IMAGE=openstorey/community-relay-agent:8.0.8.0
PROVIDER_ENROLL_TOKEN=<token-from-dashboard>
```

Keep the remaining cloud endpoint, bind, and port defaults unless the dashboard
gave you different assignments. Start both services from
`autoyou-core/autoyou-distributed/`:

```bash
docker compose up -d
docker compose logs -f provider-agent coturn
```

The agent claims the token, writes runtime secrets into the Docker volume, and
starts heartbeat and usage loops. The token should not appear in logs after
enrollment.

### What approval means

The cloud prober first checks STUN Binding, a TURN Allocate authentication
challenge, DNS, and reachability. A passing result is classified as
`turn-candidate`. Administrative approval is still required. After approval,
the next healthy heartbeat changes the provider to `active-roster`, which makes
it eligible for paid Connection clients.

### Network requirements

Forward and permit all of the following to the Docker host:

| Purpose | Protocol and port |
| --- | --- |
| STUN/TURN | UDP 3478 |
| TURN fallback | TCP 3478 |
| Optional TLS TURN | TCP 5349 |
| Relay media | UDP 49152-49200 |

Do not put ordinary Cloudflare Tunnel or an orange-cloud DNS record in front of
the TURN endpoint. HTTP tunnels do not provide the public UDP relay range that
the automated prober and WebRTC clients need. A Layer 4 product could work
only when its exact TCP/UDP ports and relay range are supported and tested.

### Troubleshooting

- `enroll-claim failed: HTTP 410`: the token was consumed or expired. Generate a
  new token in the dashboard.
- `coturn waiting for provider-agent enrollment`: inspect the agent logs and
  confirm the token, image tags, and shared Docker volumes.
- `needs-fix`: check public DNS, the host firewall, and router forwarding.
- `stun-only`: coturn answered STUN but its TURN realm or authenticated
  challenge is not correct.
- `management-only`: the submitted endpoint is only a health or tunnel URL; it
  is not a public TURN relay.

## Safety notes

Donors relay encrypted WebRTC bytes. They do not receive application message
content. Do not disable coturn's peer restrictions or its nftables rate limit
just to make a probe pass. Fix the network or image configuration instead.

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-83eee3a88da40cdba652d8f7

"""The AutoYou invite link - one object, every sharing channel.

An invite is a single URL. Shown on a screen it is a QR code; sent in a message
it is a tappable link; read aloud it degrades to the short code inside it. The
person sharing it never picks a "method", because there is only one artifact.

Where the secret lives
----------------------

The invitation passphrase is the key that decrypts the Peer Link envelopes, so
it must not reach any server. It is therefore carried in the URL **fragment**,
which browsers never transmit and web logs never see::

    https://app.autoyou.me/peer/add?i=<invitation-id>&r=<rendezvous>#k=<passphrase>

The invitation id and rendezvous host sit in the query, where the landing page
can read them - it needs both to render "someone invited you" and to hand the
invite to the app after a deferred install. That split is safe on purpose:

* the id alone is useless, because every envelope under it is encrypted;
* a party holding only the id can fetch the offer ciphertext but cannot read it;
* it cannot forge an answer either - :mod:`clients.python.peer_link.codec` binds
  each envelope with a keyed tag derived from the passphrase, so anything posted
  without the passphrase is rejected by the inviter, not by the server.

``autoyou://peer/add`` carries the identical shape for the custom-scheme path.
Both are the targets the clients already register, so an existing install opens
the invite directly.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-83eee3a88da40cdba652d8f7"


#: Public landing page. These are the exact paths the iOS and Android clients
#: already claim (``PeerLinkURL.isAddTarget``), so an existing install opens
#: directly instead of bouncing through a browser.
DEFAULT_INVITE_BASE = "https://app.autoyou.me/peer/add"

#: Staging equivalent, accepted when parsing so a test build's links resolve.
STAGING_INVITE_BASE = "https://staging-app.autoyou.me/peer/add"

#: Custom scheme both platforms already register.
APP_SCHEME_BASE = "autoyou://peer/add"

#: Hosts whose ``/peer/add`` path is a real invite target.
INVITE_HOSTS = frozenset({"app.autoyou.me", "staging-app.autoyou.me"})

#: The codec's authenticator shape: 32 bytes, base64url, unpadded.
_PASSPHRASE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_INVITATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

#: A rendezvous is either a bare https origin or the sentinel ``cloud``.
#:
#: ``[0-9]`` rather than ``\d``: on a ``str`` pattern Python's ``\d`` also
#: matches Unicode digits, so ``https://host:٩٩`` was accepted here and refused
#: by the Swift and Kotlin clients, which are ASCII-only by default. A device
#: decides where to send an encrypted envelope from this value, so the three
#: implementations have to agree, and agreeing on the stricter one is free.
_RENDEZVOUS_RE = re.compile(r"^(cloud|https://[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?)$")

#: Display names are cosmetic and attacker-supplied. Bounded, single line, and
#: never interpreted as markup by anything that renders them.
MAX_DISPLAY_NAME_CHARS = 48

_QUERY_INVITATION = "i"
_QUERY_RENDEZVOUS = "r"
_FRAGMENT_KEY = "k"
_FRAGMENT_NAME = "n"


class InviteError(ValueError):
    """Raised when an invite URL cannot be built or trusted."""


def sanitize_display_name(value: object) -> str:
    """Return a safe, bounded, single-line display name.

    The name travels in an invite an attacker may have authored, and it is shown
    on the accept sheet before any trust decision. Control characters and
    newlines are stripped so it cannot forge extra UI lines.
    """
    text = str(value or "")
    # Replace rather than delete: a newline separates words, so dropping it
    # would silently turn "Alice\nBob" into "AliceBob". Collapsing to a single
    # space keeps the name readable while removing the ability to forge a
    # second line of UI.
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    # from __debug_provenance_l__ import because
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_DISPLAY_NAME_CHARS]


@dataclass(frozen=True)
class PeerInvite:
    """A parsed invite. Every field has already been validated."""

    invitation_id: str
    passphrase: str
    rendezvous: str
    display_name: str = ""

    @property
    def uses_cloud_rendezvous(self) -> bool:
        return self.rendezvous == "cloud"


def _validate(invitation_id: str, passphrase: str, rendezvous: str) -> None:
    if not _INVITATION_ID_RE.fullmatch(invitation_id):
        raise InviteError("invitation id is not well formed")
    if not _PASSPHRASE_RE.fullmatch(passphrase):
        raise InviteError("invitation passphrase is not well formed")
    if not _RENDEZVOUS_RE.fullmatch(rendezvous):
        raise InviteError("rendezvous is not an allowed destination")


def build_invite_url(
    invitation_id: str,
    passphrase: str,
    *,
    rendezvous: str = "cloud",
    base: str = DEFAULT_INVITE_BASE,
    display_name: str = "",
) -> str:
    """Build the shareable invite URL.

    ``rendezvous`` is either ``"cloud"`` or an ``https://`` origin, which is what
    lets a self-hosted server run the whole flow without the cloud being
    involved at all.
    """
    invitation_id = str(invitation_id or "").strip()
    passphrase = str(passphrase or "").strip()
    rendezvous = str(rendezvous or "cloud").strip().rstrip("/")
    _validate(invitation_id, passphrase, rendezvous)

    parts = urlsplit(base)
    if parts.scheme not in {"https", "autoyou"}:
        raise InviteError("invite base must be https or the autoyou scheme")

    query = urlencode(
        [(_QUERY_INVITATION, invitation_id), (_QUERY_RENDEZVOUS, rendezvous)]
    )
    fragment_pairs = [(_FRAGMENT_KEY, passphrase)]
    name = sanitize_display_name(display_name)
    if name:
        fragment_pairs.append((_FRAGMENT_NAME, name))
    # quote_via keeps the fragment readable while still escaping separators.
    fragment = urlencode(fragment_pairs, quote_via=quote)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))


def build_app_scheme_url(
    invitation_id: str,
    passphrase: str,
    *,
    rendezvous: str = "cloud",
    display_name: str = "",
) -> str:
    """The ``autoyou://peer/add`` form, for platforms falling back off universal links."""
    return build_invite_url(
        invitation_id,
        passphrase,
        rendezvous=rendezvous,
        base=APP_SCHEME_BASE,
        display_name=display_name,
    )


def parse_invite_url(url: str) -> PeerInvite:
    """Parse and validate an invite URL from any of its accepted shapes.

    Raises :class:`InviteError` for anything that is not a well-formed invite,
    so a caller can treat a bad link as a single "this link is not valid"
    outcome without inspecting why.
    """
    text = str(url or "").strip()
    if not text or len(text) > 4096:
        raise InviteError("not an AutoYou invite link")

    try:
        parts = urlsplit(text)
    except ValueError as exc:  # pragma: no cover - urlsplit is lenient
        raise InviteError("not an AutoYou invite link") from exc

    if parts.scheme not in {"https", "autoyou"}:
        raise InviteError("not an AutoYou invite link")

    # Match the client's own target check: an invite is only ever the registered
    # path on a known host (or the custom scheme). Without this a look-alike
    # domain could hand a device something shaped like an invite.
    host = (parts.netloc or "").lower()
    path = (parts.path or "").rstrip("/") or "/"
    if parts.scheme == "https":
        if host not in INVITE_HOSTS or path != "/peer/add":
            raise InviteError("not an AutoYou invite link")
    elif host != "peer" or path != "/add":
        raise InviteError("not an AutoYou invite link")

    query = dict(parse_qsl(parts.query, keep_blank_values=False))
    fragment = dict(parse_qsl(parts.fragment, keep_blank_values=False))

    invitation_id = str(query.get(_QUERY_INVITATION, "")).strip()
    rendezvous = str(query.get(_QUERY_RENDEZVOUS, "cloud")).strip().rstrip("/")
    passphrase = str(fragment.get(_FRAGMENT_KEY, "")).strip()

    if not passphrase:
        # The commonest real failure: a channel that stripped the fragment, or
        # a link copied without its tail. Worth its own message because the
        # remedy is different - ask for the link again.
        raise InviteError("this invite link is missing its secret half")

    _validate(invitation_id, passphrase, rendezvous)
    return PeerInvite(
        invitation_id=invitation_id,
        passphrase=passphrase,
        rendezvous=rendezvous,
        display_name=sanitize_display_name(fragment.get(_FRAGMENT_NAME, "")),
    )


def looks_like_invite_url(value: object) -> bool:
    """True when the text is plausibly an invite, without validating it fully."""
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return lowered.startswith("autoyou://peer/add") or (
        lowered.startswith("https://")
        and "/peer/add?" in lowered
        and f"{_FRAGMENT_KEY}=" in lowered
    )


def resolve_rendezvous_base(rendezvous: str, *, cloud_base: str) -> str:
    """Turn an invite's rendezvous field into the origin to call.

    ``cloud`` resolves to the caller-supplied cloud base so that the mapping
    lives with the caller's configuration rather than inside an invite an
    attacker may have authored.
    """
    value = str(rendezvous or "").strip().rstrip("/")
    if value == "cloud":
        base = str(cloud_base or "").strip().rstrip("/")
        if not base.startswith("https://"):
            raise InviteError("cloud rendezvous base must be https")
        return base
    if not _RENDEZVOUS_RE.fullmatch(value):
        raise InviteError("rendezvous is not an allowed destination")
    return value


__all__ = [
    "APP_SCHEME_BASE",
    "DEFAULT_INVITE_BASE",
    "INVITE_HOSTS",
    "STAGING_INVITE_BASE",
    "MAX_DISPLAY_NAME_CHARS",
    "InviteError",
    "PeerInvite",
    "build_app_scheme_url",
    "build_invite_url",
    "looks_like_invite_url",
    "parse_invite_url",
    "resolve_rendezvous_base",
    "sanitize_display_name",
]

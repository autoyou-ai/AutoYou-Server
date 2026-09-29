# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-c97556b080829327eb29ac6d

"""Self-signed local CA + server certificate management for opt-in HTTPS.

AutoYou's admin / auth / page web services normally speak plain HTTP on the
loopback (and, when the operator opts in, the LAN). This module lets those
services be served over TLS instead, using a **per-install root CA** and a
short-lived **leaf server certificate** whose SubjectAltNames cover loopback,
the machine's current LAN IPv4/IPv6 addresses and its host name.

Why a private CA (and the one manual step it implies)
-----------------------------------------------------
No operating system trusts a self-generated certificate automatically - that is
the entire point of the OS trust model, and Safari, iOS and Android will each
refuse an untrusted certificate. So a client only gets "real" HTTPS authenticity
here after its user installs and trusts this CA (downloadable from ``/ca.crt``).
That trust step is deliberately manual and cannot be silently automated.

Design choices
--------------
* **EC P-256 keys** - modern, fast, and trusted by every current Apple/Android/
  browser stack.
* **Leaf validity capped at 397 days** - Apple platforms reject TLS *server*
  certificates whose validity exceeds 398 days, so a longer leaf would be
  untrusted on iOS/macOS even after the CA is trusted. (The CA cert itself is
  exempt from that rule and is issued for ~10 years.)
* **Stable CA, reissued leaf** - the CA key/cert persist across enable/disable so
  clients never have to re-trust; only the leaf is reissued when the LAN address
  set changes or it nears expiry. ``purge()`` fully removes everything for a
  clean reset.

The module has no AutoYou dependencies and is safe to import anywhere.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import datetime
import ipaddress
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-c97556b080829327eb29ac6d"


_CA_KEY_NAME = "ca.key"
_CA_CERT_NAME = "ca.crt"
_LEAF_KEY_NAME = "server.key"
_LEAF_CERT_NAME = "server.crt"

_CA_VALID_DAYS = 3650          # ~10 years; CA certs are exempt from the 398-day rule.
_LEAF_VALID_DAYS = 397         # < 398 so Apple platforms accept the server cert.
_LEAF_REISSUE_SLACK_DAYS = 30  # reissue when this close to expiry.
_ORG_NAME = "AutoYou (local)"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def tls_dir(config_dir) -> Path:
    """Directory (created on demand) where the CA and leaf material live."""
    directory = Path(config_dir) / "tls"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory


# ---------------------------------------------------------------------------
# SubjectAltName discovery
# ---------------------------------------------------------------------------
def _local_hostnames() -> List[str]:
    names = ["localhost"]
    try:
        host = socket.gethostname().strip()
    except OSError:
        host = ""
    for candidate in (host, f"{host}.local" if host and not host.endswith(".local") else ""):
        candidate = candidate.strip()
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _local_ip_addresses() -> List[str]:
    """Best-effort set of this host's own IP addresses (loopback + LAN)."""
    addresses = {"127.0.0.1", "::1"}
    try:
        hostname = socket.gethostname()
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                for info in socket.getaddrinfo(hostname, None, family):
                    addresses.add(info[4][0].split("%", 1)[0])
            except socket.gaierror:
                pass
    except OSError:
        pass
    # The UDP-connect trick reliably reveals the primary outbound LAN IPv4.
    for probe in ("8.8.8.8", "1.1.1.1"):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((probe, 80))
                addresses.add(sock.getsockname()[0])
            finally:
                sock.close()
        except OSError:
            continue
    cleaned = []
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        # Only bind SANs to addresses that make sense for local/LAN access, and
        # never embed a globally-routable (public) address in the certificate.
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            cleaned.append(str(ip))
    return sorted(set(cleaned))


def san_entries() -> Tuple[List[str], List[str]]:
    """Return ``(dns_names, ip_addresses)`` for the leaf certificate."""
    return _local_hostnames(), _local_ip_addresses()


def _san_general_names(dns_names: List[str], ip_addresses: List[str]) -> List[x509.GeneralName]:
    names: List[x509.GeneralName] = [x509.DNSName(name) for name in dns_names]
    for raw in ip_addresses:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(raw)))
        except ValueError:
            continue
    return names


# ---------------------------------------------------------------------------
# Key / cert IO
# ---------------------------------------------------------------------------
def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        path.chmod(0o644)
    except OSError:
        pass


def _load_key(path: Path) -> Optional[ec.EllipticCurvePrivateKey]:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError):
        return None
    return key if isinstance(key, ec.EllipticCurvePrivateKey) else None


def _load_cert(path: Path) -> Optional[x509.Certificate]:
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CA + leaf issuance
# ---------------------------------------------------------------------------
def _build_ca(directory: Path) -> Tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key_path = directory / _CA_KEY_NAME
    cert_path = directory / _CA_CERT_NAME
    key = _load_key(key_path)
    cert = _load_cert(cert_path)
    if key is not None and cert is not None and cert.not_valid_after_utc > _utcnow():
        return cert, key

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, _ORG_NAME),
        x509.NameAttribute(NameOID.COMMON_NAME, "AutoYou Local Root CA"),
    ])
    now = _utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_CA_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(key_path, key)
    _write_cert(cert_path, cert)
    return cert, key


def _issue_leaf(
    directory: Path,
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    dns_names: List[str],
    ip_addresses: List[str],
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    now = _utcnow()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_names[0] if dns_names else "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_LEAF_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(_san_general_names(dns_names, ip_addresses)), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(directory / _LEAF_KEY_NAME, key)
    _write_cert(directory / _LEAF_CERT_NAME, cert)


def _leaf_covers(cert: x509.Certificate, dns_names: List[str], ip_addresses: List[str]) -> bool:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    cert_dns = set(san.get_values_for_type(x509.DNSName))
    cert_ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    return set(dns_names).issubset(cert_dns) and set(ip_addresses).issubset(cert_ips)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@dataclass
class TLSMaterial:
    ca_cert_path: Path
    server_cert_path: Path
    server_key_path: Path
    dns_names: List[str] = field(default_factory=list)
    ip_addresses: List[str] = field(default_factory=list)
    not_valid_after: Optional[datetime.datetime] = None
    fingerprint_sha256: str = ""


def ensure_enabled(config_dir) -> TLSMaterial:
    """Idempotently ensure a CA and a *current* leaf certificate exist.

    Reissues the leaf when the machine's LAN address set changed or the leaf is
    within ``_LEAF_REISSUE_SLACK_DAYS`` of expiry. Returns the paths uvicorn
    needs (``ssl_certfile`` / ``ssl_keyfile``) plus metadata for the admin UI.
    """
    directory = tls_dir(config_dir)
    ca_cert, ca_key = _build_ca(directory)

    dns_names, ip_addresses = san_entries()
    leaf_cert_path = directory / _LEAF_CERT_NAME
    leaf_key_path = directory / _LEAF_KEY_NAME
    leaf = _load_cert(leaf_cert_path)
    leaf_key = _load_key(leaf_key_path)

    stale = (
        leaf is None
        or leaf_key is None
        or leaf.not_valid_after_utc <= _utcnow() + datetime.timedelta(days=_LEAF_REISSUE_SLACK_DAYS)
        or not _leaf_covers(leaf, dns_names, ip_addresses)
    )
    if stale:
        _issue_leaf(directory, ca_cert, ca_key, dns_names, ip_addresses)
        leaf = _load_cert(leaf_cert_path)

    fingerprint = ""
    not_after = None
    if leaf is not None:
        fingerprint = leaf.fingerprint(hashes.SHA256()).hex()
        not_after = leaf.not_valid_after_utc
    return TLSMaterial(
        ca_cert_path=directory / _CA_CERT_NAME,
        server_cert_path=leaf_cert_path,
        server_key_path=leaf_key_path,
        dns_names=dns_names,
        ip_addresses=ip_addresses,
        not_valid_after=not_after,
        fingerprint_sha256=fingerprint,
    )


def ca_certificate_pem(config_dir) -> Optional[bytes]:
    """CA certificate PEM bytes for the ``/ca.crt`` download endpoint, or None."""
    cert = _load_cert(tls_dir(config_dir) / _CA_CERT_NAME)
    return cert.public_bytes(serialization.Encoding.PEM) if cert is not None else None


def status(config_dir) -> dict:
    """Lightweight status for the admin UI / diagnostics."""
    directory = tls_dir(config_dir)
    ca_cert = _load_cert(directory / _CA_CERT_NAME)
    leaf = _load_cert(directory / _LEAF_CERT_NAME)
    # from __debug_provenance_t__ import address
    return {
        "ca_present": ca_cert is not None,
        "server_cert_present": leaf is not None,
        "ca_fingerprint_sha256": ca_cert.fingerprint(hashes.SHA256()).hex() if ca_cert else "",
        "server_fingerprint_sha256": leaf.fingerprint(hashes.SHA256()).hex() if leaf else "",
        "server_not_valid_after": leaf.not_valid_after_utc.isoformat() if leaf else "",
        "sans": {
            "dns": list(san_entries()[0]),
            "ip": list(san_entries()[1]),
        },
    }


def disable(config_dir) -> None:
    """Offboard the *leaf* so the service stops presenting a server cert.

    The CA is kept so re-enabling never forces clients to re-trust. Use
    :func:`purge` for a full reset that also removes the CA.
    """
    directory = tls_dir(config_dir)
    for name in (_LEAF_CERT_NAME, _LEAF_KEY_NAME):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def purge(config_dir) -> None:
    """Remove all local-TLS material (CA + leaf). Clients must re-trust after."""
    directory = tls_dir(config_dir)
    for name in (_LEAF_CERT_NAME, _LEAF_KEY_NAME, _CA_CERT_NAME, _CA_KEY_NAME):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

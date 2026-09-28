# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Unit tests for shared.update_service (network-free, generated keypair)."""

from __future__ import annotations

import base64
import json
import hashlib
import plistlib
import shlex
from pathlib import Path
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shared.update_service import (
    MAX_MANIFEST_BYTES,
    UpdateArtifact,
    UpdateAuthError,
    UpdateError,
    UpdateService,
    platform_key,
)

def _keypair():
    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return priv, base64.b64encode(pub_raw).decode("ascii")

def _envelope(priv, payload: dict, *, key_id: str = "default") -> dict:
    payload_str = json.dumps(payload, sort_keys=True)
    sig = priv.sign(payload_str.encode("utf-8"))
    return {
        "payload": payload_str,
        "signature": base64.b64encode(sig).decode("ascii"),
        "algorithm": "ed25519",
        "key_id": key_id,
    }

def _service(pub_b64_by_id: dict, **kwargs) -> UpdateService:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    trusted = {
        kid: Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))
        for kid, b64 in pub_b64_by_id.items()
    }
    return UpdateService(trusted_keys=trusted, **kwargs)


def _manifest(**overrides):
    payload = {
        "schema_version": 1,
        "product": "autoyou-server",
        "version": "2.0.0",
        "channel": "stable",
        "artifacts": {},
    }
    payload.update(overrides)
    return payload

def test_platform_key_shape():
    key = platform_key()
    assert isinstance(key, str) and "-" in key

def test_verify_envelope_accepts_valid_signature():
    priv, pub = _keypair()
    svc = _service({"default": pub})
    payload = {"version": "1.2.3", "channel": "stable", "artifacts": {}}
    parsed = svc.verify_envelope(_envelope(priv, payload))
    assert parsed["version"] == "1.2.3"

def test_verify_envelope_rejects_tampered_payload():
    priv, pub = _keypair()
    svc = _service({"default": pub})
    env = _envelope(priv, {"version": "1.2.3", "channel": "stable"})
    env["payload"] = env["payload"].replace("1.2.3", "9.9.9")  # tamper after signing
    with pytest.raises(UpdateError):
        svc.verify_envelope(env)

def test_verify_envelope_fail_closed_without_trusted_key(monkeypatch):
    monkeypatch.delenv("AUTOYOU_UPDATE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("AUTOYOU_UPDATE_PUBLIC_KEYS", raising=False)
    priv, _pub = _keypair()
    svc = UpdateService(trusted_keys={})  # no trusted keys at all
    with pytest.raises(UpdateError):
        svc.verify_envelope(_envelope(priv, {"version": "1.0.0"}))

def test_verify_envelope_rejects_unknown_key_id():
    priv, pub = _keypair()
    svc = _service({"release-1": pub})
    with pytest.raises(UpdateError):
        svc.verify_envelope(_envelope(priv, {"version": "1.0.0"}, key_id="default"))

def test_verify_envelope_rejects_non_ed25519():
    priv, pub = _keypair()
    svc = _service({"default": pub})
    env = _envelope(priv, {"version": "1.0.0"})
    env["algorithm"] = "rsa"
    with pytest.raises(UpdateError):
        svc.verify_envelope(env)

def test_check_for_update_version_comparison(monkeypatch):
    monkeypatch.setenv("AUTOYOU_VERSION", "1.0.0")
    priv, pub = _keypair()
    svc = _service({"default": pub})

    newer = svc.parse_manifest(_manifest(version="1.4.0"))
    same = svc.parse_manifest(_manifest(version="1.0.0"))
    older = svc.parse_manifest(_manifest(version="0.9.0"))

    assert svc.check_for_update(manifest=newer)["update_available"] is True
    assert svc.check_for_update(manifest=same)["update_available"] is False
    assert svc.check_for_update(manifest=older)["update_available"] is False

def test_fetch_manifest_via_injected_http(monkeypatch):
    monkeypatch.setenv("AUTOYOU_VERSION", "1.0.0")
    priv, pub = _keypair()
    payload = _manifest(
        artifacts={
            platform_key(): {"url": "https://x/AutoYou.zip", "sha256": "ab" * 32, "size": 10}
        },
    )
    body = json.dumps(_envelope(priv, payload)).encode("utf-8")
    svc = _service({"default": pub}, http_get=lambda url, timeout: body)
    info = svc.check_for_update()
    assert info["latest_version"] == "2.0.0"
    assert info["update_available"] is True
    assert info["artifact_available"] is True


def test_manifest_fetch_rejects_oversized_response_before_parsing():
    svc = UpdateService(
        trusted_keys={},
        http_get=lambda *_: b"x" * (MAX_MANIFEST_BYTES + 1),
    )
    with pytest.raises(UpdateError, match="1 MiB"):
        svc.fetch_manifest()


def test_default_manifest_fetch_stops_at_response_limit(monkeypatch):
    import requests

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, *, chunk_size):
            assert chunk_size > 0
            yield b"x" * MAX_MANIFEST_BYTES
            yield b"x"
            raise AssertionError("manifest download continued past response limit")

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: Response())
    with pytest.raises(UpdateError, match="1 MiB"):
        UpdateService(trusted_keys={})._default_http_get(
            "https://app.autoyou.me/v1/updates/autoyou-server/stable/latest.json",
            1.0,
        )


def test_manifest_artifact_requires_signed_size():
    with pytest.raises(UpdateError, match="valid byte size"):
        UpdateService.parse_manifest(
            _manifest(
                artifacts={
                    "linux-x64": {
                        "url": "https://app.autoyou.me/v1/update-artifacts/AutoYou.tar.gz",
                        "sha256": "ab" * 32,
                    }
                }
            )
        )

def test_simulated_version_makes_the_update_available_path_reachable(monkeypatch):
    """Release QA needs the "update available" state without cutting a release."""
    monkeypatch.setenv("AUTOYOU_UPDATE_SIMULATED_CURRENT_VERSION", "8.0.4.0")
    svc = UpdateService(trusted_keys={}, current_version="8.0.8.0")

    assert svc.current_version() == "8.0.4.0"
    assert svc._version_gt("8.0.8.0", svc.current_version()) is True


def test_simulated_version_is_ignored_when_unset_or_malformed(monkeypatch):
    monkeypatch.delenv("AUTOYOU_UPDATE_SIMULATED_CURRENT_VERSION", raising=False)
    assert UpdateService(trusted_keys={}, current_version="8.0.8.0").current_version() == "8.0.8.0"

    monkeypatch.setenv("AUTOYOU_UPDATE_SIMULATED_CURRENT_VERSION", "; rm -rf /")
    assert UpdateService(trusted_keys={}, current_version="8.0.8.0").current_version() == "8.0.8.0"


def test_simulated_version_never_relaxes_manifest_verification(monkeypatch):
    """The override changes what we claim to be, not what we trust."""
    monkeypatch.setenv("AUTOYOU_UPDATE_SIMULATED_CURRENT_VERSION", "0.0.1")
    priv, _pub = _keypair()
    other, other_pub = _keypair()
    body = json.dumps(_envelope(priv, _manifest(version="9.9.9"))).encode()

    svc = _service({"default": other_pub}, http_get=lambda url, timeout: body)
    with pytest.raises(UpdateError, match="signature verification failed"):
        svc.fetch_manifest()


def test_source_checkout_resolves_the_repository_root_not_the_shared_package():
    """`shared/update_service.py` is one level below the app root.

    Anchoring on the module's own directory hides the checkout's `.git`, which
    would route every source install into the packaged installer-download path.
    """
    import shared.update_service as module

    repo_root = Path(module.__file__).resolve().parent.parent

    assert UpdateService._resolve_app_root() == repo_root
    assert (repo_root / ".git").exists()
    assert UpdateService(trusted_keys={}).install_kind() == "source"


def test_feed_rejection_is_reported_as_an_account_link_failure():
    """A gated feed answering 401/403 means "re-link", not "feed is broken"."""
    import urllib.error

    def unauthorized(url, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    svc = UpdateService(trusted_keys={}, http_get=unauthorized)
    with pytest.raises(UpdateAuthError, match="Reconnect this AutoYou account"):
        svc.fetch_manifest()


def test_requests_style_forbidden_response_is_an_auth_error():
    class Response:
        status_code = 403

    class HttpError(Exception):
        response = Response()

    def forbidden(url, timeout):
        raise HttpError("403 Client Error: Forbidden")

    svc = UpdateService(trusted_keys={}, http_get=forbidden)
    with pytest.raises(UpdateAuthError):
        svc.fetch_manifest()


def test_non_auth_transport_failure_stays_a_plain_update_error():
    def offline(url, timeout):
        raise OSError("name or service not known")

    svc = UpdateService(trusted_keys={}, http_get=offline)
    with pytest.raises(UpdateError, match="Could not fetch update manifest") as excinfo:
        svc.fetch_manifest()
    assert not isinstance(excinfo.value, UpdateAuthError)


def test_server_error_is_not_mistaken_for_a_rejected_credential():
    import urllib.error

    def unavailable(url, timeout):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

    svc = UpdateService(trusted_keys={}, http_get=unavailable)
    with pytest.raises(UpdateError) as excinfo:
        svc.fetch_manifest()
    assert not isinstance(excinfo.value, UpdateAuthError)


def test_artifact_download_rejection_is_an_auth_error(tmp_path):
    import urllib.error

    def unauthorized(url, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    svc = UpdateService(trusted_keys={}, http_get=unauthorized, app_root=tmp_path)
    artifact = UpdateArtifact(
        platform_key="linux-x64",
        url="https://app.autoyou.me/v1/update-artifacts/autoyou-server/9.0.0/AutoYou.tar.gz",
        sha256="ab" * 32,
        size=10,
    )
    with pytest.raises(UpdateAuthError):
        svc.download_artifact(artifact)


def test_manifest_url_building():
    svc = UpdateService(trusted_keys={}, feed_base="https://h/updates", channel="beta")
    assert svc.manifest_url() == "https://h/updates/beta/latest.json"
    assert svc.manifest_url("stable") == "https://h/updates/stable/latest.json"


def test_default_feed_is_product_scoped(monkeypatch):
    monkeypatch.setenv("AUTOYOU_UPDATE_FEED_BASE", "https://h/v1/updates")
    svc = UpdateService(trusted_keys={}, product="autoyou-connect", channel="stable")
    assert svc.manifest_url() == "https://h/v1/updates/autoyou-connect/stable/latest.json"


def test_disabled_service_makes_no_http_request():
    called = False

    def should_not_run(url, timeout):
        nonlocal called
        called = True
        return b"{}"

    svc = UpdateService(trusted_keys={}, enabled=False, http_get=should_not_run)
    with pytest.raises(UpdateError, match="disabled"):
        svc.fetch_manifest()
    assert called is False


def test_process_opt_out_wins_over_config(monkeypatch):
    monkeypatch.setenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", "0")
    svc = UpdateService(trusted_keys={}, enabled=True, http_get=lambda *_: b"{}")
    with pytest.raises(UpdateError, match="disabled"):
        svc.fetch_manifest()


def test_manifest_fetch_rejects_non_autoyou_host_before_http():
    called = False

    def should_not_run(*_):
        nonlocal called
        called = True
        return b"{}"

    svc = UpdateService(
        trusted_keys={},
        feed_base="https://updates.example.test/v1/updates/autoyou-server",
        http_get=should_not_run,
    )
    with pytest.raises(UpdateError, match="not authorized"):
        svc.fetch_manifest()
    assert called is False


def test_bearer_header_is_normalized():
    svc = UpdateService(trusted_keys={}, auth_token="synthetic-token", cookie_header="session=synthetic")
    assert svc._request_headers() == {
        "Authorization": "Bearer synthetic-token",
        "Cookie": "session=synthetic",
    }


def test_credentials_are_not_forwarded_to_another_artifact_origin():
    svc = UpdateService(trusted_keys={}, auth_token="synthetic-token", cookie_header="session=synthetic")
    assert svc._request_headers("https://cdn.example.test/AutoYou.exe") == {}
    assert svc._request_headers("https://app.autoyou.me/v1/update-artifacts/AutoYou.exe")["Authorization"] == "Bearer synthetic-token"


def test_urllib_fallback_rejects_cross_origin_redirect_with_credentials():
    reached_target = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            reached_target.append((self.headers.get("Authorization"), self.headers.get("Cookie")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"must-not-reach")

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/capture")
            self.end_headers()

        def log_message(self, *_args):
            pass

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (target, redirect)]
    for thread in threads:
        thread.start()
    try:
        origin = f"http://127.0.0.1:{redirect.server_port}"
        svc = UpdateService(
            trusted_keys={},
            feed_base=f"{origin}/v1/updates/autoyou-server",
            auth_token="synthetic-token",
            cookie_header="session=synthetic",
        )
        with pytest.raises(UpdateError, match="cross-origin redirect"):
            with svc._urllib_open(f"{origin}/redirect", 2.0) as response:
                response.read()
        assert reached_target == []
    finally:
        for server in (redirect, target):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_manifest_scope_is_bound_to_product_and_channel():
    svc = UpdateService(trusted_keys={}, product="autoyou-connect", channel="stable")
    wrong_product = svc.parse_manifest(_manifest())
    with pytest.raises(UpdateError, match="product mismatch"):
        svc.check_for_update(manifest=wrong_product)
    wrong_channel = svc.parse_manifest(_manifest(product="autoyou-connect", channel="beta"))
    with pytest.raises(UpdateError, match="channel mismatch"):
        svc.check_for_update(manifest=wrong_channel)


def test_manifest_requires_explicit_supported_channel():
    payload = _manifest()
    payload.pop("channel")
    with pytest.raises(UpdateError, match="supported 'channel'"):
        UpdateService.parse_manifest(payload)


def test_manifest_version_matches_core_artifact_route():
    with pytest.raises(UpdateError, match="route-safe 'version'"):
        UpdateService.parse_manifest(_manifest(version="release"))

def test_plan_origin_main_sync_shape():
    svc = UpdateService(trusted_keys={})
    plan = svc.plan_origin_main_sync()
    assert plan == [["git", "fetch", "origin", "main"], ["git", "merge", "--ff-only", "origin/main"]]

    commit = "1" * 40
    manifest = svc.parse_manifest(_manifest(git={"remote": "origin", "branch": "main", "commit": commit}))
    assert svc.plan_origin_main_sync(manifest) == [
        ["git", "fetch", "origin", "main"],
        ["git", "merge-base", "--is-ancestor", commit, "origin/main"],
        ["git", "merge-base", "--is-ancestor", "HEAD", commit],
        ["git", "merge", "--ff-only", commit],
    ]


def test_manifest_rejects_non_object_id_git_commit():
    with pytest.raises(UpdateError, match="full hexadecimal object id"):
        UpdateService.parse_manifest(_manifest(git={"remote": "origin", "branch": "main", "commit": "main"}))

def test_sha256_file(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"autoyou-update")
    assert UpdateService.sha256_file(p) == hashlib.sha256(b"autoyou-update").hexdigest()

def test_download_artifact_discards_on_sha_mismatch(tmp_path, monkeypatch):
    svc = UpdateService(trusted_keys={}, http_get=lambda url, timeout: b"not-the-expected-bytes")
    monkeypatch.setattr(svc, "staging_dir", lambda: tmp_path)
    art = UpdateArtifact(
        platform_key=platform_key(),
        url="https://x/A.zip",
        sha256="00" * 32,
        size=len(b"not-the-expected-bytes"),
    )
    with pytest.raises(UpdateError):
        svc.download_artifact(art)
    assert list(tmp_path.glob("A.zip")) == []  # mismatched download removed


def test_streamed_artifact_stops_at_signed_size(tmp_path, monkeypatch):
    import requests

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, *, chunk_size):
            assert chunk_size > 0
            yield b"1234"
            yield b"5"
            raise AssertionError("download continued after exceeding signed size")

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: Response())
    svc = UpdateService(trusted_keys={})
    monkeypatch.setattr(svc, "staging_dir", lambda: tmp_path)
    artifact = UpdateArtifact(
        platform_key=platform_key(),
        url="https://app.autoyou.me/v1/update-artifacts/A.zip",
        sha256=hashlib.sha256(b"1234").hexdigest(),
        size=4,
    )
    with pytest.raises(UpdateError, match="exceeded signed size"):
        svc.download_artifact(artifact)
    assert not (tmp_path / "A.zip").exists()


def test_apply_packaged_update_downloads_verifies_and_launches(tmp_path, monkeypatch):
    from shared.update_service import UpdateArtifact

    body = b"signed-installer-bytes"
    launched = []
    svc = UpdateService(
        trusted_keys={},
        current_version="1.0.0",
        http_get=lambda url, timeout: body,
        launcher=launched.append,
    )
    monkeypatch.setattr(svc, "install_kind", lambda: "packaged")
    monkeypatch.setattr(svc, "staging_dir", lambda: tmp_path)
    manifest = svc.parse_manifest(_manifest(
        artifacts={
            platform_key(): {
                "url": "https://app.autoyou.me/v1/update-artifacts/AutoYou.exe",
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
            }
        },
    ))
    result = svc.apply_update(manifest=manifest)
    assert result["installer_launched"] is True
    assert launched == [tmp_path / "AutoYou.exe"]


def test_docker_update_returns_host_compose_command(monkeypatch):
    svc = UpdateService(trusted_keys={}, current_version="1.0.0")
    monkeypatch.setattr(svc, "install_kind", lambda: "docker")
    commit = "1" * 40
    manifest = svc.parse_manifest(_manifest(git={"remote": "origin", "branch": "main", "commit": commit}))
    result = svc.apply_update(manifest=manifest)
    assert result["requires_host"] is True
    assert result["commands"] == [
        "if git status --porcelain | grep -q .; then exit 1; fi",
        "git fetch origin main",
        f"git merge-base --is-ancestor {commit} origin/main",
        f"git merge-base --is-ancestor HEAD {commit}",
        f"git merge --ff-only {commit}",
        f"git merge-base --is-ancestor HEAD {commit}",
        f"git merge-base --is-ancestor {commit} HEAD",
        "docker compose up -d --build autoyou",
    ]
    assert result["command"] == (
        'git -c "alias.autoyou-update=!'
        + " && ".join(result["commands"])
        + '" autoyou-update'
    )
    assert "\n" not in result["command"]


def test_docker_update_requires_signed_commit():
    svc = UpdateService(trusted_keys={}, current_version="1.0.0")
    manifest = svc.parse_manifest(_manifest())

    with pytest.raises(UpdateError, match="no pinned git commit"):
        svc.docker_update_command(manifest)


def test_source_update_requires_signed_manifest():
    svc = UpdateService(trusted_keys={}, current_version="1.0.0")

    with pytest.raises(UpdateError, match="require a signed manifest"):
        svc.apply_origin_main_sync(None)


def test_packaged_version_resolves_from_pyinstaller_data_root(tmp_path, monkeypatch):
    from shared import version

    module = tmp_path / "_internal" / "shared" / "version.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (tmp_path / "_internal" / "VERSION").write_text("8.0.8.0\n", encoding="utf-8")
    monkeypatch.delenv("AUTOYOU_VERSION", raising=False)
    monkeypatch.delenv("AUTOYOU_VERSION_FILE", raising=False)
    monkeypatch.setattr(version, "__file__", str(module))

    assert version.get_version() == "8.0.8.0"


def test_source_update_rejects_ahead_or_dirty_checkout(tmp_path, monkeypatch):
    def git(cwd, *args):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    ahead = tmp_path / "ahead"
    dirty = tmp_path / "dirty"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(seed))
    git(seed, "config", "user.name", "Synthetic Update Test")
    git(seed, "config", "user.email", "update-test@example.test")
    (seed / "release.txt").write_text("signed\n", encoding="utf-8")
    git(seed, "add", "release.txt")
    git(seed, "commit", "-m", "signed release")
    signed_commit = git(seed, "rev-parse", "HEAD")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "-b", "main", str(remote), str(ahead))
    git(ahead, "config", "user.name", "Synthetic Update Test")
    git(ahead, "config", "user.email", "update-test@example.test")
    (ahead / "local.txt").write_text("unsigned descendant\n", encoding="utf-8")
    git(ahead, "add", "local.txt")
    git(ahead, "commit", "-m", "local descendant")

    manifest = UpdateService.parse_manifest(
        _manifest(git={"remote": "origin", "branch": "main", "commit": signed_commit})
    )
    service = UpdateService(trusted_keys={}, app_root=ahead)
    monkeypatch.setattr(service, "install_kind", lambda: "source")
    with pytest.raises(UpdateError, match="not an ancestor"):
        service.apply_origin_main_sync(manifest)

    git(tmp_path, "clone", "-b", "main", str(remote), str(dirty))
    (dirty / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    service = UpdateService(trusted_keys={}, app_root=dirty)
    monkeypatch.setattr(service, "install_kind", lambda: "source")
    with pytest.raises(UpdateError, match="uncommitted changes"):
        service.apply_origin_main_sync(manifest)


def test_pyinstaller_linux_update_extracts_beside_frozen_bundle(tmp_path, monkeypatch):
    from shared import platform_runtime

    bundle = tmp_path / "dist" / "AutoYou Connect"
    executable = bundle / "AutoYou Connect"
    module = bundle / "_internal" / "shared" / "update_service.py"
    module.parent.mkdir(parents=True)
    executable.touch()
    module.touch()
    monkeypatch.setattr(platform_runtime, "get_platform", lambda: "linux")
    monkeypatch.setattr(platform_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(platform_runtime.sys, "executable", str(executable))

    assert platform_runtime.get_application_root(module) == bundle
    service = UpdateService(trusted_keys={})
    action = service._posix_archive_install_action(tmp_path / "AutoYou-Connect.tar.gz")
    assert service._app_root == bundle
    assert f"--directory {shlex.quote(str(bundle.parent))}" in action["command"]


def test_manifest_rejects_untrusted_artifact_transport():
    with pytest.raises(UpdateError, match="HTTPS"):
        UpdateService.parse_manifest(_manifest(
            artifacts={
                "linux-x64": {"url": "http://updates.example.test/AutoYou.tar.gz", "sha256": "ab" * 32}
            },
        ))


@pytest.mark.parametrize("receipt", [False, True])
def test_store_detection_follows_the_actual_executable_through_nested_helpers(tmp_path, monkeypatch, receipt):
    from shared import macos_runtime_support as macos
    from shared.update_service import native_store_uri

    contents = tmp_path / "AutoYou.app/Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"AutoYouAppStoreBuild": not receipt}))
    if receipt:
        (contents / "_MASReceipt").mkdir()
        (contents / "_MASReceipt/receipt").touch()
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    for path in ("MacOS/AutoYou", "Resources/AutoYouRuntime/AutoYouServer",
                 "Helpers/AutoYou Helper.app/Contents/MacOS/Helper"):
        executable = contents / path
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        monkeypatch.setattr(macos.sys, "executable", str(executable))
        assert macos.is_app_store_build()
        for product in ("autoyou-server", "autoyou-connect", "autoyou-lite"):
            assert native_store_uri(product) == "macappstore://showUpdatesPage"

    monkeypatch.setattr(macos.sys, "platform", "linux")
    assert not macos.is_app_store_build()
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    monkeypatch.setattr(macos.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setenv("AUTOYOU_PACKAGED_RESOURCES_ROOT", str(contents / "Resources"))
    assert not macos.is_app_store_build(), "An unrelated resources path must not change update ownership"

    # A direct build with absent, malformed, or non-boolean metadata stays direct.
    monkeypatch.setattr(macos.sys, "executable", str(contents / "MacOS/AutoYou"))
    (contents / "_MASReceipt/receipt").unlink(missing_ok=True)
    for metadata in (b"invalid", plistlib.dumps({}), plistlib.dumps({"AutoYouAppStoreBuild": "true"})):
        (contents / "Info.plist").write_bytes(metadata)
        assert not macos.is_app_store_build()


def test_store_managed_updates_never_use_the_feed_or_separate_installers(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Store builds must not request or install a separate update")

    svc = UpdateService(trusted_keys={}, current_version="1.0.0", enabled=False,
                        app_root=tmp_path, http_get=forbidden, launcher=forbidden)
    opened = []
    monkeypatch.setattr("shared.update_service.is_app_store_build", lambda: True)
    monkeypatch.setattr(svc, "_launch_native_store", opened.append)
    monkeypatch.setattr(svc, "staging_dir", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    status = svc.check_for_update()
    assert status["latest_version"] is None and status["update_available"] is None
    assert status["store_managed"] and not status["artifact_available"]
    assert not opened, "A status check must not open the Store"
    result = svc.apply_update()
    assert result["store_opened"] is True
    assert opened == ["macappstore://showUpdatesPage"]
    manifest = svc.parse_manifest(_manifest())
    artifact = UpdateArtifact(platform_key="macos-arm64", url="https://app.autoyou.me/synthetic.pkg", sha256="ab" * 32, size=1)
    for action in (svc.fetch_manifest, lambda: svc.download_artifact(artifact),
                   lambda: svc.stage_packaged_update(manifest=manifest),
                   lambda: svc.install_staged_update(tmp_path / "synthetic.pkg"),
                   lambda: svc.install_staged_update(tmp_path / "synthetic.whl"),
                   lambda: svc.apply_origin_main_sync(manifest)):
        with pytest.raises(UpdateError, match="managed by the app store"):
            action()
    assert not list(tmp_path.iterdir())
    def failed_open(_uri):
        raise subprocess.CalledProcessError(1, ["open", "macappstore://showUpdatesPage"])
    monkeypatch.setattr(svc, "_launch_native_store", failed_open)
    with pytest.raises(UpdateError, match="Could not open the app store"):
        svc.apply_update()


def test_wsl_archive_returns_safe_restart_install_command(tmp_path, monkeypatch):
    body = b"synthetic archive"
    staging = tmp_path / "staged updates"
    staging.mkdir()
    svc = UpdateService(
        trusted_keys={},
        app_root=tmp_path / "AutoYouServer",
        current_version="1.0.0",
        http_get=lambda *_: body,
    )
    monkeypatch.setattr(svc, "install_kind", lambda: "packaged")
    monkeypatch.setattr(svc, "staging_dir", lambda: staging)
    monkeypatch.setattr("shared.update_service.platform_key", lambda: "wsl-x64")
    manifest = svc.parse_manifest(_manifest(artifacts={
        "wsl-x64": {
            "url": "https://app.autoyou.me/v1/update-artifacts/AutoYou.tar.gz",
            "sha256": hashlib.sha256(body).hexdigest(),
            "size": len(body),
        }
    }))
    result = svc.apply_update(manifest=manifest)
    assert result["status"] == "action_required"
    assert "tar --extract --gzip" in result["command"]
    assert "staged updates" in result["command"]

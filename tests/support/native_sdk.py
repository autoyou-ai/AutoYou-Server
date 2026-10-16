"""Shared real SDK fixture; storage always remains in each test's scoped root."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import tempfile

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SERVER = _ROOT / "AutoYou-Server-Iroh" if (_ROOT / "AutoYou-Server-Iroh/server.py").is_file() else _ROOT


def stage_native_runtime(root: Path, sdk: Path) -> Path:
    from shared.iroh_binding import target_tag
    from shared.iroh_release import IROH_VERSION

    root.mkdir(parents=True, exist_ok=True)
    config = root / "fixture-release.json"
    config.write_text(json.dumps(dict(schema_version=1, generation="iroh", version=IROH_VERSION,
        policy=dict(bind_addresses=["0.0.0.0:0"], brokered_relays=True, allow_lan_peers=True,
                    relays=[dict(url="https://relay.example.invalid/")]),
        core=dict(issuer="https://core.example.invalid", public_key="11" * 32))))
    stage = runpy.run_path(str(_SERVER / "scripts/stage_iroh_runtime.py"))["stage_runtime"]
    stage(root, generation="iroh", sdk=sdk, config=config, target=target_tag())
    return root / "transport/iroh" / target_tag()


@pytest.fixture(scope="session")
def protected_state_sdk(autoyou_test_root):
    configured = os.environ.get("AUTOYOU_IROH_SDK_DIR")
    if not configured:
        pytest.skip("Explicit owned SDK required for native protected-state checks")
    from shared.iroh_binding import load_binding
    root = Path(tempfile.mkdtemp(prefix="owned-native-runtime-", dir=autoyou_test_root))
    binding = stage_native_runtime(root, Path(configured))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("AUTOYOU_TEST_ROOT", str(autoyou_test_root))
        api = load_binding(artifact_root=binding)
    lock = _SERVER / "transport-rust/Cargo.lock"
    assert api.core_info().lock_sha256 == hashlib.sha256(lock.read_bytes()).hexdigest()
    return api


@pytest.fixture(autouse=True)
def native_session_identity(request, monkeypatch):
    if os.environ.get("AUTOYOU_IROH_SDK_DIR"):
        api = request.getfixturevalue("protected_state_sdk")
        monkeypatch.setattr("shared.session_execution._native_api", lambda: api)
        monkeypatch.setattr("shared.session_transport._native_api", lambda: api)
        monkeypatch.setattr("shared.runtime_limits.native_api", lambda: api)
        monkeypatch.setattr("shared.native_secure_stream.native_api", lambda: api.application_json)

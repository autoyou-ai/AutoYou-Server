# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-2d2030786345634238313737-3b69ac461cafc6e7ba379e98

"""Regression coverage for Secure Professional Maximus at-rest protection."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-2d2030786345634238313737-3b69ac461cafc6e7ba379e98"


import sqlite3
import os
import sys
import time
from io import BytesIO
from pathlib import Path

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import shared.secure_storage as secure_storage
from shared.secure_storage import (
    FILE_HEADER,
    SQLITE_HEADER,
    SecureStorageError,
    disable_secure_storage,
    enable_secure_storage,
    load_secure_json,
    materialize_secure_file,
    read_secure_file,
    recover_stranded_envelopes,
    rotate_secure_storage,
    save_secure_json,
    seal_secure_paths,
    sealed_envelope_kind,
    secure_storage_enabled,
    find_sealed_envelopes,
    unseal_secure_storage,
    write_secure_file,
)


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch: pytest.MonkeyPatch):
    """Keep the process-wide SQLite adapter and key fallback test-local."""

    disable_secure_storage()
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    yield
    disable_secure_storage()


def _enable(root, password: str = "synthetic-spm-password"):
    return enable_secure_storage(
        app_name="AutoYou-test",
        root=root,
        password=password,
    )


def test_file_json_and_path_materialization_are_encrypted(tmp_path):
    protected = tmp_path / "mutable.json"
    protected.write_bytes(b'{"value":"synthetic-value"}')

    status = _enable(tmp_path)
    assert status["enabled"] is True
    assert status["key_source"] == "password_fallback"

    assert read_secure_file(protected) == b'{"value":"synthetic-value"}'
    on_disk = protected.read_bytes()
    assert on_disk.startswith(FILE_HEADER)
    assert b"synthetic-value" not in on_disk

    save_secure_json(protected, {"value": "round-trip"})
    assert load_secure_json(protected) == {"value": "round-trip"}

    with materialize_secure_file(protected) as materialized:
        assert materialized != protected
        assert load_secure_json(materialized) == {"value": "round-trip"}
    assert not materialized.exists()


def test_external_plaintext_can_be_read_without_migration(tmp_path):
    external = tmp_path / "external.jsonl"
    payload = b'{"synthetic":"external"}\n'
    external.write_bytes(payload)
    _enable(tmp_path)

    assert read_secure_file(external, migrate_plaintext=False) == payload
    assert external.read_bytes() == payload


def test_disabled_storage_cannot_overwrite_a_sealed_file(tmp_path):
    protected = tmp_path / "protected.json"
    _enable(tmp_path)
    write_secure_file(protected, b"synthetic-secret")
    sealed = protected.read_bytes()

    disable_secure_storage()
    with pytest.raises(SecureStorageError, match="requires Secure Professional Maximus"):
        write_secure_file(protected, b"replacement")

    assert protected.read_bytes() == sealed


def test_worker_reattaches_existing_password_boundary_without_keyring(tmp_path, monkeypatch):
    """Docker/WSL workers reuse the established fallback boundary, never enrol one."""
    root = tmp_path / "maximus"
    protected = root / "state.bin"
    _enable(root, password="synthetic-worker-password")
    write_secure_file(protected, b"synthetic-worker-state")
    disable_secure_storage()

    monkeypatch.setenv(
        "AUTOYOU_SECURE_STORAGE_MODE",
        secure_storage.SECURE_PROFESSIONAL_MAXIMUS_MODE,
    )
    monkeypatch.setenv("AUTOYOU_SECURE_STORAGE_ROOT", str(root))
    monkeypatch.setenv("AUTOYOU_SECURE_STORAGE_APP", "AutoYou-test")
    monkeypatch.setenv("AUTOYOU_SECURE_STORAGE_PASSWORD", "synthetic-worker-password")

    status = secure_storage.enable_secure_storage_from_environment()

    assert status and status["key_source"] == "password_fallback"
    assert secure_storage.read_secure_file(protected) == b"synthetic-worker-state"


def test_webrtc_temp_media_is_encrypted_and_cleaned_after_use(tmp_path, monkeypatch):
    from shared import openclaw_gateway, voice_messaging

    media_root = tmp_path / "autoyou_media"
    monkeypatch.setattr(openclaw_gateway, "_get_temp_media_dir", lambda: str(media_root))
    monkeypatch.setattr(voice_messaging.tempfile, "gettempdir", lambda: str(tmp_path))
    _enable(tmp_path / "storage")

    payload = b"synthetic-m4a-payload"
    saved = openclaw_gateway.write_bytes_to_temp(
        payload,
        filename="voice-message-synthetic.m4a",
        source="ios",
        user_id="synthetic-user",
        session_id="synthetic-session",
    )
    path = Path(saved["path"])
    on_disk = path.read_bytes()

    assert path == media_root / "ios" / "synthetic-user" / "synthetic-session" / "voice-message-synthetic.m4a"
    assert on_disk.startswith(FILE_HEADER)
    assert payload not in on_disk
    assert read_secure_file(path) == payload
    rotated_on_disk = path.read_bytes()
    rotation = rotate_secure_storage(
        password="synthetic-rotated-m4a-password",
        scan_roots=[media_root],
    )
    assert rotation["files_rekeyed"] >= 1
    assert path.read_bytes() != rotated_on_disk
    assert read_secure_file(path) == payload

    voice_messaging.cleanup_paths(str(path))
    assert not path.exists()


def test_storage_key_rotation_reencrypts_files_and_sqlite_for_new_password(tmp_path):
    root = tmp_path / "storage"
    password = "synthetic-initial-password"
    rotated_password = "synthetic-rotated-password"
    protected = root / "secret.json"
    database = root / "records.db"

    _enable(root, password=password)
    write_secure_file(protected, b"synthetic-rotated-payload")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES (?)", ("synthetic-before-rotation",))

    old_ciphertext = protected.read_bytes()
    old_salt = (root / secure_storage.KEY_SALT_FILE).read_bytes()
    result = rotate_secure_storage(password=rotated_password, scan_roots=[root])

    assert result["key_source"] == "password_fallback"
    assert result["files_rekeyed"] >= 1
    assert result["databases_rekeyed"] >= 1
    assert protected.read_bytes() != old_ciphertext
    assert (root / secure_storage.KEY_SALT_FILE).read_bytes() != old_salt
    assert read_secure_file(protected) == b"synthetic-rotated-payload"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "synthetic-before-rotation"

    disable_secure_storage()
    _enable(root, password=rotated_password)
    assert read_secure_file(protected) == b"synthetic-rotated-payload"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1


def test_eager_seal_migrates_dormant_plaintext_owned_stores(tmp_path):
    """Owned stores that predate enable must be sealable without waiting on access.

    Regression for the live finding: under Maximus, agent_security.db, registries,
    and other owned stores sat in plaintext because migrate-on-access had not yet
    reopened them. seal_secure_paths force-migrates them immediately.
    """
    root = tmp_path / "storage"
    root.mkdir(parents=True)
    # Plaintext owned stores written OUTSIDE the adapter (as if predating enable).
    registry = root / "agent_install_registry.json"
    registry.write_text('{"installed_agents": ["synthetic-agent"]}', encoding="utf-8")
    security_db = root / "agent_security.db"
    raw = sqlite3.connect(str(security_db))
    raw.execute("CREATE TABLE profiles (secret TEXT NOT NULL)")
    raw.execute("INSERT INTO profiles VALUES (?)", ("synthetic-2fa-secret",))
    raw.commit()
    raw.close()
    assert security_db.read_bytes().startswith(b"SQLite format 3")

    _enable(root)
    # Both are still plaintext right after enable (migrate-on-access, untouched).
    assert not registry.read_bytes().startswith(FILE_HEADER)
    assert security_db.read_bytes().startswith(b"SQLite format 3")

    result = seal_secure_paths([registry, security_db, root / "does-not-exist.db"])
    assert result["sealed"] == 2
    assert result["skipped"] >= 1  # the missing path

    # Now sealed on disk, no cleartext secret, still readable through the adapter.
    assert registry.read_bytes().startswith(FILE_HEADER)
    assert b"synthetic-agent" not in registry.read_bytes()
    assert load_secure_json(registry) == {"installed_agents": ["synthetic-agent"]}
    assert security_db.read_bytes().startswith(SQLITE_HEADER)
    assert b"synthetic-2fa-secret" not in security_db.read_bytes()
    with sqlite3.connect(security_db) as connection:
        assert connection.execute("SELECT secret FROM profiles").fetchone()[0] == "synthetic-2fa-secret"

    # Idempotent: a second sweep seals nothing new.
    again = seal_secure_paths([registry, security_db])
    assert again["sealed"] == 0


def test_eager_seal_covers_agent_ui_sessions_file(tmp_path):
    """agent_ui_sessions.json (OTP mission/chat/shared session tokens) must be
    sealable the same way as the other owned control-plane stores.

    Regression for the gap found while adding the opt-in shared cross-agent
    session: server.py's eager-seal list omitted this file, so under Maximus
    the per-agent OTP session tokens sat in plaintext at rest.
    """
    root = tmp_path / "storage"
    root.mkdir(parents=True)
    sessions_file = root / "agent_ui_sessions.json"
    sessions_file.write_text(
        '{"donation_agent": {"synthetic-live-token": 9999999999.0}}', encoding="utf-8"
    )

    _enable(root)
    assert not sessions_file.read_bytes().startswith(FILE_HEADER)  # still plaintext, untouched

    result = seal_secure_paths([sessions_file])
    assert result["sealed"] == 1
    assert sessions_file.read_bytes().startswith(FILE_HEADER)
    assert b"synthetic-live-token" not in sessions_file.read_bytes()
    assert load_secure_json(sessions_file) == {"donation_agent": {"synthetic-live-token": 9999999999.0}}


def test_rotation_and_unseal_skip_foreign_key_envelope_instead_of_aborting(tmp_path):
    """One envelope sealed by a DIFFERENT boundary/key must not brick rotation.

    Regression for the live finding: the full server's scan root contained a
    file sealed by autoyou_lite's separate key; rotation tried to decrypt it
    with its own key, hit InvalidToken, and aborted the WHOLE operation (so the
    operator could never rotate or downgrade). Now such orphans are skipped and
    reported while every healthy store is still processed.
    """
    from cryptography.fernet import Fernet

    root = tmp_path / "storage"
    root.mkdir(parents=True)
    good = root / "good.json"
    foreign = root / "foreign-from-other-boundary.bin"

    _enable(root)
    save_secure_json(good, {"value": "synthetic-healthy"})
    assert good.read_bytes().startswith(FILE_HEADER)
    # An envelope produced by a DIFFERENT key (as autoyou_lite would), header-valid
    # but undecryptable by this boundary.
    other_key = Fernet.generate_key()
    foreign.write_bytes(FILE_HEADER + Fernet(other_key).encrypt(b"synthetic-foreign-bytes"))

    # Rotation skips the foreign envelope, re-keys the healthy one, and reports it.
    result = rotate_secure_storage(scan_roots=[root])
    assert result["files_rekeyed"] >= 1
    assert str(foreign) in result["skipped_undecryptable"]
    assert load_secure_json(good) == {"value": "synthetic-healthy"}  # survived
    assert foreign.read_bytes().startswith(FILE_HEADER)  # left untouched

    # Downgrade also skips it and still unseals the healthy store to plaintext.
    un = unseal_secure_storage(scan_roots=[root])
    assert str(foreign) in un["skipped_undecryptable"]
    assert not good.read_bytes().startswith(FILE_HEADER)  # now plaintext
    assert b"synthetic-healthy" in good.read_bytes()
    assert foreign.read_bytes().startswith(FILE_HEADER)  # orphan stays sealed


def test_downgrade_unseals_files_and_sqlite_back_to_plaintext(tmp_path):
    """Switching Maximus -> Normal must decrypt owned stores, not brick them.

    Without unseal, the sealed envelopes stay on disk and fail closed in Normal
    (SQLite reports "file is not a database"; read_secure_file raises).
    """
    root = tmp_path / "storage"
    protected = root / "agent_ui_sessions.json"
    database = root / "login_ui_state.db"

    _enable(root)
    save_secure_json(protected, {"session": "synthetic-token-xyz"})
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE scores (v TEXT NOT NULL)")
        connection.execute("INSERT INTO scores VALUES (?)", ("synthetic-high-score",))

    # Sealed on disk before downgrade.
    assert protected.read_bytes().startswith(FILE_HEADER)
    assert database.read_bytes().startswith(SQLITE_HEADER)

    result = unseal_secure_storage(scan_roots=[root])
    assert result["enabled"] is False
    assert result["files_unsealed"] >= 2
    assert result["databases_unsealed"] >= 1
    assert secure_storage_enabled() is False

    # Plaintext on disk, no ciphertext headers, secret survives.
    json_bytes = protected.read_bytes()
    assert not json_bytes.startswith(FILE_HEADER)
    assert b"synthetic-token-xyz" in json_bytes
    db_bytes = database.read_bytes()
    assert db_bytes.startswith(b"SQLite format 3")
    assert not db_bytes.startswith(SQLITE_HEADER)

    # Readable through the ordinary (now-unpatched) stdlib APIs.
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT v FROM scores").fetchone()[0] == "synthetic-high-score"


def test_rotation_rekeys_agent_db_whose_dir_name_is_not_discovery_skipped(tmp_path):
    """Owned agent DBs must survive rotation even when opened by another process.

    Regression: the discovery walk skipped a directory literally named
    ``mac_security_agent`` (its data dir leaf), so a rotation triggered from a
    process that had not opened that DB re-keyed everything else and orphaned it
    under the old key -> the next open raised. win_security_agent (not skipped)
    was fine; the asymmetry was the bug. The agent uses plain ``sqlite3.connect``.
    """
    root = tmp_path / "storage"
    root.mkdir(parents=True)
    for agent_dir in ("mac_security_agent", "win_security_agent"):
        database = root / agent_dir / "network.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)

        _enable(root)
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE snapshots (v TEXT NOT NULL)")
            connection.execute("INSERT INTO snapshots VALUES (?)", (f"synthetic-{agent_dir}",))
        assert database.read_bytes().startswith(SQLITE_HEADER)

        # Simulate a fresh process (e.g. rotation from the admin parent while the
        # worker holds this DB): drop the in-memory registry so discovery must
        # find the envelope by walking, then re-enter with the same key.
        disable_secure_storage()
        _enable(root)
        result = rotate_secure_storage(scan_roots=[root])
        assert result["files_rekeyed"] >= 1, f"{agent_dir} DB was not discovered for rotation"

        # Must still open + decrypt under the NEW key.
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT v FROM snapshots").fetchone()[0] == f"synthetic-{agent_dir}"
        disable_secure_storage()


def test_storage_key_rotation_replaces_os_keystore_key(tmp_path, monkeypatch):
    keys = {}

    def fake_get_or_create_key(service, username, *, create=True):
        key = keys.get((service, username))
        if key is None and create:
            key = b"synthetic-os-key-".ljust(32, b"!")
            keys[(service, username)] = key
        return key

    def fake_replace_key(service, username, raw_key=None):
        keys[(service, username)] = bytes(raw_key or b"")
        return True

    monkeypatch.setattr(secure_storage, "keyring_available", lambda: True)
    monkeypatch.setattr(secure_storage, "get_or_create_key", fake_get_or_create_key)
    # The storage path reports the backend without rereading the same Keychain
    # credential. Keep this test off the real Keychain while preserving that
    # single-read contract.
    monkeypatch.setattr(secure_storage, "_backend_name", lambda: "synthetic-keychain")
    monkeypatch.setattr(secure_storage, "replace_key", fake_replace_key)

    root = tmp_path / "keychain-storage"
    protected = root / "keychain-secret.bin"
    _enable(root, password="unused-fallback-password")
    write_secure_file(protected, b"synthetic-keychain-payload")
    service_key = next(iter(keys))
    old_key = keys[service_key]

    result = rotate_secure_storage(scan_roots=[root])

    assert result["key_source"] == "os_keychain"
    assert result["files_rekeyed"] >= 1
    assert keys[service_key] != old_key
    assert read_secure_file(protected) == b"synthetic-keychain-payload"


def test_plaintext_sqlite_is_migrated_and_reopened_through_standard_api(tmp_path):
    database = tmp_path / "records.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES (?)", ("synthetic-record",))

    _enable(tmp_path)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "synthetic-record"
        connection.execute("INSERT INTO records VALUES (?)", ("second-record",))

    on_disk = database.read_bytes()
    assert on_disk.startswith(SQLITE_HEADER)
    assert b"synthetic-record" not in on_disk
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2


def test_wal_marked_sqlite_snapshot_reopens_after_restart(tmp_path):
    root = tmp_path / "storage"
    database = root / "sessions.db"
    _enable(root)

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES (?)", ("synthetic-wal-record",))

    state = secure_storage._DATABASES[database.resolve()]
    payload = state.anchor.serialize()
    wal_payload = payload[:18] + b"\x02\x02" + payload[20:]
    context = secure_storage._require_context()
    secure_storage._atomic_write(
        database,
        SQLITE_HEADER + context.fernet.encrypt(wal_payload),
    )

    state.anchor.close()
    secure_storage._DATABASES.clear()
    disable_secure_storage()
    _enable(root)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "synthetic-wal-record"


def test_owned_notes_attachment_and_database_use_same_boundary(tmp_path):
    from autoyou_agents.notes_agent.notes_tool import NotesTool

    _enable(tmp_path)
    database = tmp_path / "notes.db"
    tool = NotesTool(db_path=str(database))
    attachment = tool.save_media_attachment(
        "synthetic.bin",
        "c3ludGhldGljLWJ5dGVz",  # base64("synthetic-bytes")
        mimetype="application/octet-stream",
    )

    attachment_path = tmp_path / "media" / "synthetic.bin"
    assert attachment_path.read_bytes().startswith(FILE_HEADER)
    assert read_secure_file(attachment_path) == b"synthetic-bytes"
    assert database.read_bytes().startswith(SQLITE_HEADER)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT size_bytes FROM media_attachments WHERE id = ?",
            (attachment["id"],),
        ).fetchone()[0] == len(b"synthetic-bytes")


def test_legacy_independent_persona_ciphertext_migrates_into_spm(tmp_path):
    from shared.secure_data_store import ENC_HEADER, SecureDataStore

    key = b"synthetic-persona-key"
    keys = {("synthetic-persona", "default"): key.ljust(32, b"!")}

    def provider(service, username, create):
        if (service, username) not in keys and create:
            keys[(service, username)] = b"synthetic-persona-new-key".ljust(32, b"!")
        return keys.get((service, username))

    persona_path = tmp_path / "persona.md"
    legacy = SecureDataStore(
        persona_path,
        encrypt=True,
        keystore_service="synthetic-persona",
        key_provider=provider,
    )
    legacy.write("synthetic persona data")
    assert persona_path.read_text(encoding="utf-8").startswith(ENC_HEADER)

    _enable(tmp_path)
    assert legacy.read() == "synthetic persona data"
    assert persona_path.read_bytes().startswith(FILE_HEADER)


def test_owned_http_media_readers_decrypt_and_migrate_legacy_files(tmp_path, monkeypatch):
    from autoyou_agents.notes_agent.website.backend.app import _iter_file_range
    from autoyou_agents.page_agent.website.backend.app import PageFeedService
    from autoyou_agents.voice_training_agent.website.backend import app as voice_app

    _enable(tmp_path)
    payload = b"synthetic-http-media"

    notes_path = tmp_path / "notes.wav"
    notes_path.write_bytes(payload)
    assert b"".join(_iter_file_range(notes_path, 0, len(payload) - 1)) == payload
    assert notes_path.read_bytes().startswith(FILE_HEADER)

    page_path = tmp_path / "page.bin"
    page_path.write_bytes(payload)
    assert PageFeedService._read_blob_bytes(page_path) == payload
    assert page_path.read_bytes().startswith(FILE_HEADER)

    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    recording_path = recordings_dir / "legacy.wav"
    recording_path.write_bytes(payload)
    monkeypatch.setattr(voice_app, "_check_auth", lambda request: True)
    monkeypatch.setattr(voice_app, "_get_paths", lambda: (tmp_path, tmp_path, recordings_dir))
    response = voice_app.serve_recording("legacy.wav", object())
    assert response.body == payload
    assert recording_path.read_bytes().startswith(FILE_HEADER)


def test_partner_pending_delivery_indexes_use_the_shared_boundary(tmp_path):
    from signal_service import SignalService
    from shared.scheduler_service import load_json, save_json
    from telegram_user_service import TelegramUserService
    from whatsapp_service import WhatsAppNodeService

    _enable(tmp_path)
    synthetic_entry = {"queued_at": time.time(), "payload": {"text": "synthetic pending"}}

    signal = object.__new__(SignalService)
    signal.data_dir = str(tmp_path / "signal")
    signal._pending_voice_reply_limit = 20
    signal._pending_media_reply_limit = 20
    signal._pending_voice_replies = [synthetic_entry]
    signal._pending_media_replies = [synthetic_entry]
    signal._persist_pending_voice_replies()
    signal._persist_pending_media_replies()

    whatsapp = object.__new__(WhatsAppNodeService)
    whatsapp._pending_voice_reply_file = tmp_path / "whatsapp" / "pending_voice_replies.json"
    whatsapp._pending_media_reply_file = tmp_path / "whatsapp" / "pending_media_replies.json"
    whatsapp._pending_voice_reply_limit = 20
    whatsapp._pending_media_reply_limit = 20
    whatsapp._pending_voice_replies = [synthetic_entry]
    whatsapp._pending_media_replies = [synthetic_entry]
    whatsapp._persist_pending_voice_replies()
    whatsapp._persist_pending_media_replies()

    telegram = object.__new__(TelegramUserService)
    telegram.data_dir = tmp_path / "telegram"
    telegram._pending_media_replies = [synthetic_entry]
    telegram._persist_pending_media_replies()

    for path in (
        tmp_path / "signal" / "pending_voice_replies.json",
        tmp_path / "signal" / "pending_media_replies.json",
        tmp_path / "whatsapp" / "pending_voice_replies.json",
        tmp_path / "whatsapp" / "pending_media_replies.json",
        tmp_path / "telegram" / "pending_media_replies.json",
    ):
        assert path.read_bytes().startswith(FILE_HEADER)

    assert signal._load_pending_voice_replies() == [synthetic_entry]
    assert whatsapp._load_pending_voice_replies() == [synthetic_entry]
    assert telegram._load_pending_media_replies() == [synthetic_entry]

    session_index = tmp_path / "agent_ui_sessions.json"
    save_json(session_index, {"synthetic-agent": {"synthetic-token": time.time() + 60}})
    assert session_index.read_bytes().startswith(FILE_HEADER)
    assert "synthetic-agent" in load_json(session_index)


def test_wrong_password_fails_closed_without_plaintext_fallback(tmp_path):
    protected = tmp_path / "secret.bin"
    _enable(tmp_path, password="first-synthetic-password")
    secure_storage.write_secure_file(protected, b"synthetic-secret")
    disable_secure_storage()

    _enable(tmp_path, password="different-synthetic-password")
    with pytest.raises(SecureStorageError):
        read_secure_file(protected)


def test_video_recording_seals_snapshots_and_mp4_when_available(tmp_path):
    from shared import video_call_manager
    from shared.video_call_manager import IncomingVideoTrackSink

    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")
    if video_call_manager.INBOUND_VIDEO_RECORDING_FORMAT != "mp4_video" or video_call_manager._av is None:
        pytest.skip("PyAV MP4 recording is not available")

    _enable(tmp_path)
    image = video_call_manager.Image.new("RGB", (32, 18), (10, 20, 30))
    jpeg = BytesIO()
    image.save(jpeg, format="JPEG")
    sink = IncomingVideoTrackSink(
        object(),
        session_id="synthetic-video-session",
        recording_enabled=True,
        recording_dir=str(tmp_path),
        recording_mode="video",
    )
    sink._record_frame(jpeg.getvalue(), 32, 18, image)
    sink._close_recording()

    recording_dir = Path(sink.recording_path or "")
    assert recording_dir.is_dir()
    recording_path = recording_dir / "video.mp4"
    manifest_path = recording_dir / "manifest.jsonl"
    assert recording_path.read_bytes().startswith(FILE_HEADER)
    assert manifest_path.read_bytes().startswith(FILE_HEADER)
    assert b"synthetic-video-session" not in recording_path.read_bytes()

    with video_call_manager._av.open(BytesIO(read_secure_file(recording_path))) as container:
        assert next(container.decode(video=0)).width == 32


def test_fine_tuning_dataset_jsonl_uses_shared_boundary(tmp_path):
    from autoyou_agents.fine_tuning_agent.training_data import write_jsonl
    from autoyou_agents.fine_tuning_agent.training_runner import _read_jsonl

    _enable(tmp_path)
    dataset_path = tmp_path / "workspace" / "datasets" / "synthetic" / "train.jsonl"
    rows = [{"messages": [{"role": "assistant", "content": "synthetic reply"}]}]

    assert write_jsonl(dataset_path, rows) == 1
    assert dataset_path.read_bytes().startswith(FILE_HEADER)
    assert b"synthetic reply" not in dataset_path.read_bytes()
    assert _read_jsonl(dataset_path) == rows


def test_fine_tuning_worker_logs_are_encrypted(tmp_path):
    from autoyou_agents.fine_tuning_agent.fine_tuning_tool import (
        _finish_logged_process,
        _start_logged_process,
        _tail_file,
    )

    _enable(tmp_path)
    log_path = tmp_path / "workspace" / "runs" / "synthetic.log"
    process, thread, errors = _start_logged_process(
        [sys.executable, "-c", "print('synthetic worker line')"],
        cwd=str(tmp_path),
        env=os.environ.copy(),
        log_path=log_path,
    )
    _finish_logged_process(process, thread, errors)

    assert errors == []
    on_disk = log_path.read_bytes()
    assert on_disk.startswith(FILE_HEADER)
    assert b"synthetic worker line" not in on_disk
    assert "synthetic worker line" in _tail_file(log_path)


def test_lite_accepts_maximus_name_but_keeps_existing_pairing_wire_mode():
    from tests.support.paths import ensure_autoyou_lite_on_path

    ensure_autoyou_lite_on_path()
    from autoyou_lite.config import LibConfig

    config = LibConfig(
        {
            "security_mode": "secure-professional-maximus",
            "password": "synthetic-lite-password",
        }
    )
    assert config.security_mode == "secure_professional_maximus"
    assert config.pairing_router_mode() == "secure_professional"


def test_lite_encrypted_config_reconnects_to_protected_store_after_restart(tmp_path):
    from tests.support.paths import ensure_autoyou_lite_on_path

    ensure_autoyou_lite_on_path()
    from autoyou_lite._runtime.encrypted_json_store import EncryptedJsonStore

    password = "synthetic-lite-config-password"
    store = EncryptedJsonStore(
        tmp_path / "config.encrypted",
        default_factory=dict,
    )
    enable_secure_storage(
        app_name="AutoYouLite",
        root=tmp_path,
        password=password,
    )
    store.save(
        {
            "security_mode": "secure_professional_maximus",
            "password": password,
            "synthetic_value": "protected",
        },
        password,
    )
    assert store.path.read_bytes().startswith(FILE_HEADER)

    disable_secure_storage()
    assert not secure_storage_enabled()
    assert store.load(password)["synthetic_value"] == "protected"
    assert secure_storage_enabled()


def test_prompt_override_control_files_migrate_through_shared_boundary(tmp_path, monkeypatch):
    from shared.platform_runtime import (
        JAILBREAK_ACKNOWLEDGEMENT_FILENAME,
        JAILBREAK_ROOT_PROMPT_FILENAME,
        get_jailbreak_data_dir,
        get_jailbreak_root_prompt,
        is_jailbreak_active,
        sign_jailbreak_root_prompt,
    )

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    storage_root = tmp_path / "AutoYou"
    prompt_dir = get_jailbreak_data_dir(anchor=__file__)
    ack_path = prompt_dir / JAILBREAK_ACKNOWLEDGEMENT_FILENAME
    prompt_path = prompt_dir / JAILBREAK_ROOT_PROMPT_FILENAME
    ack_path.write_text("synthetic prompt override consent", encoding="utf-8")
    prompt_path.write_text("synthetic prompt instructions", encoding="utf-8")
    # The override is only honoured when signed by the admin save path; this
    # test covers the secure-storage migration boundary, not authorization.
    sign_jailbreak_root_prompt("synthetic prompt instructions", anchor=__file__)

    _enable(storage_root)
    assert is_jailbreak_active(anchor=__file__) is True
    assert get_jailbreak_root_prompt(anchor=__file__) == "synthetic prompt instructions"
    assert ack_path.read_bytes().startswith(FILE_HEADER)
    assert prompt_path.read_bytes().startswith(FILE_HEADER)


def test_stranded_envelopes_recovered_when_downgrade_skipped_the_unseal(tmp_path):
    """A downgrade applied in another process must still be repairable here.

    When the mode change lands while this process never had the boundary live -
    a restart, or a config/keystore reset - unseal_secure_storage is skipped and
    the envelopes stay sealed. Every plaintext reader then fails closed, which is
    how a notes database ends up reporting "file is not a database".
    """
    root = tmp_path / "storage"
    database = root / "notes_agent" / "autoyou_notes.db"
    database.parent.mkdir(parents=True, exist_ok=True)

    _enable(root)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE notes (title TEXT NOT NULL)")
    connection.execute("INSERT INTO notes VALUES (?)", ("synthetic-kept-note",))
    connection.commit()
    connection.close()
    disable_secure_storage()  # process exits WITHOUT unsealing

    assert database.read_bytes().startswith(SQLITE_HEADER)

    report = recover_stranded_envelopes(
        app_name="AutoYou-test",
        root=root,
        scan_roots=[root],
        password="synthetic-spm-password",
    )

    assert report["stranded"] >= 1
    assert report["recovered"] >= 1
    assert report["unrecoverable"] == []
    assert report["restart_required"] is True
    assert secure_storage_enabled() is False

    # Plaintext on disk and the row survived the round trip.
    assert database.read_bytes().startswith(b"SQLite format 3")
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT title FROM notes").fetchall() == [("synthetic-kept-note",)]
    connection.close()


def test_stranded_recovery_never_mints_a_new_key_when_the_old_one_is_gone(tmp_path):
    """Losing the key must not escalate into destroying the data.

    Minting a fresh key here would make the envelopes permanently undecryptable
    and write marker files that make the loss look deliberate. Report and leave.
    """
    root = tmp_path / "storage"
    database = root / "autoyou_notes.db"

    _enable(root)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE notes (title TEXT NOT NULL)")
    connection.commit()
    connection.close()
    disable_secure_storage()

    sealed_bytes = database.read_bytes()
    for marker in (secure_storage.KEY_MODE_FILE, secure_storage.KEY_SALT_FILE):
        (root / marker).unlink(missing_ok=True)
    before = sorted(path.name for path in root.iterdir())

    report = recover_stranded_envelopes(app_name="AutoYou-test", root=root, scan_roots=[root])

    assert report["stranded"] == 1
    assert report["recovered"] == 0
    assert report["unrecoverable"] == [str(database)]
    assert report["restart_required"] is False
    assert database.read_bytes() == sealed_bytes          # untouched, not destroyed
    assert sorted(path.name for path in root.iterdir()) == before  # no new key material
    assert secure_storage_enabled() is False


def test_stranded_recovery_is_a_noop_on_plaintext_roots(tmp_path):
    """Normal startup must not pay for, or be disturbed by, the recovery sweep."""
    root = tmp_path / "storage"
    root.mkdir(parents=True)
    plain = root / "page_feed.db"
    connection = sqlite3.connect(plain)
    connection.execute("CREATE TABLE pages (v TEXT)")
    connection.commit()
    connection.close()
    original = plain.read_bytes()

    report = recover_stranded_envelopes(app_name="AutoYou-test", root=root, scan_roots=[root])

    assert report == {
        "stranded": 0,
        "recovered": 0,
        "unrecoverable": [],
        "restart_required": False,
    }
    assert plain.read_bytes() == original
    assert secure_storage_enabled() is False


def test_sealed_envelope_discovery_skips_source_directories(tmp_path):
    """Source startup should not walk code trees looking for runtime envelopes."""
    root = tmp_path / "storage"
    source_dir = root / "tests"
    runtime_dir = root / "output"
    source_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (source_dir / "ignored.db").write_bytes(SQLITE_HEADER + b"synthetic-ciphertext")
    (runtime_dir / "found.db").write_bytes(SQLITE_HEADER + b"synthetic-ciphertext")

    assert find_sealed_envelopes([root]) == [runtime_dir / "found.db"]


def test_sealed_envelope_kind_distinguishes_sealed_from_corrupt(tmp_path):
    """Readers need to tell a sealed store apart from a damaged one."""
    sealed_db = tmp_path / "sealed.db"
    sealed_file = tmp_path / "sealed.json"
    plain = tmp_path / "plain.db"
    corrupt = tmp_path / "corrupt.db"

    sealed_db.write_bytes(SQLITE_HEADER + b"synthetic-ciphertext")
    sealed_file.write_bytes(FILE_HEADER + b"synthetic-ciphertext")
    plain.write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
    corrupt.write_bytes(b"this is not a database at all")

    assert sealed_envelope_kind(sealed_db) == "sqlite"
    assert sealed_envelope_kind(sealed_file) == "file"
    assert sealed_envelope_kind(plain) is None
    assert sealed_envelope_kind(corrupt) is None
    assert sealed_envelope_kind(tmp_path / "missing.db") is None

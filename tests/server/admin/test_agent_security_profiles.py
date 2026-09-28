# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for per-agent secondary 2FA security profiles."""

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from shared.agent_security_profiles import (
    AgentSecurityProfileStore,
    SecurityProfileError,
)

pyotp = pytest.importorskip("pyotp")


@pytest.fixture()
def store(tmp_path):
    return AgentSecurityProfileStore(
        tmp_path / "agent_security.db", password="hunter2"
    )


def _code_for(enrolment):
    return pyotp.TOTP(enrolment["manual_entry_secret"]).now()


def test_create_returns_enrolment_once_and_verifies(store):
    enrol = store.create_profile("Persona 2FA")
    assert enrol["profile_id"]
    assert enrol["provisioning_uri"].startswith("otpauth://totp/")
    assert enrol["manual_entry_secret"]
    assert store.verify(enrol["profile_id"], _code_for(enrol)) is True
    assert store.verify(enrol["profile_id"], "000000") is False


def test_secret_is_never_exposed_after_creation(store):
    enrol = store.create_profile("Persona 2FA")
    profiles = store.list_profiles()
    assert len(profiles) == 1
    meta = profiles[0]
    # No path in the public metadata exposes the secret or provisioning URI.
    assert meta["secret_visible"] is False
    assert meta["has_secret"] is True
    assert "manual_entry_secret" not in meta
    assert "provisioning_uri" not in meta
    assert "secret_enc" not in meta
    assert meta["secret_stored"] is False


def test_secret_is_not_stored_at_rest(store, tmp_path):
    enrol = store.create_profile("Persona 2FA")
    raw_db_bytes = (tmp_path / "agent_security.db").read_bytes()
    # Derive-from-password: the secret is never persisted in any form. Neither the
    # base32 secret nor its salt-equivalent appears as the secret in the DB file.
    assert enrol["manual_entry_secret"].encode("utf-8") not in raw_db_bytes


def test_wrong_password_cannot_verify(tmp_path):
    db = tmp_path / "agent_security.db"
    s1 = AgentSecurityProfileStore(db, password="right")
    enrol = s1.create_profile("Persona 2FA")
    code = _code_for(enrol)
    # A different password derives a different secret from the same salt -> no match.
    s2 = AgentSecurityProfileStore(db, password="wrong")
    assert s2.verify(enrol["profile_id"], code) is False


def test_same_password_same_salt_rederives_secret(tmp_path):
    db = tmp_path / "agent_security.db"
    s1 = AgentSecurityProfileStore(db, password="stable-pw")
    enrol = s1.create_profile("Persona 2FA")
    code = _code_for(enrol)
    # A fresh store instance (same password, same persisted salt) re-derives the
    # identical secret and verifies - proving nothing secret needed to persist.
    s2 = AgentSecurityProfileStore(db, password="stable-pw")
    assert s2.verify(enrol["profile_id"], code) is True


def test_current_code_is_live_without_exposing_the_derived_secret(store):
    enrol = store.create_profile("Test authenticator")
    assert store.verify(enrol["profile_id"], _code_for(enrol)) is True
    result = store.current_code(enrol["profile_id"])
    assert pyotp.TOTP(enrol["manual_entry_secret"]).verify(result["code"], valid_window=1)
    assert 1 <= result["seconds_remaining"] <= 30
    assert set(result) == {"code", "seconds_remaining"}
    assert enrol["manual_entry_secret"] not in str(result)


def test_current_code_requires_verified_profile(store):
    enrol = store.create_profile("Unverified authenticator")
    with pytest.raises(SecurityProfileError, match="verify"):
        store.current_code(enrol["profile_id"])


def test_empty_password_rejected(tmp_path):
    with pytest.raises(SecurityProfileError):
        AgentSecurityProfileStore(tmp_path / "x.db", password="")


def test_multiple_independent_profiles(store):
    a = store.create_profile("Profile A")
    b = store.create_profile("Profile B")
    assert a["profile_id"] != b["profile_id"]
    assert store.verify(a["profile_id"], _code_for(a)) is True
    # A code for A must not verify against B.
    assert store.verify(b["profile_id"], _code_for(a)) is False


def test_assign_and_verify_for_agent(store):
    enrol = store.create_profile("Persona 2FA")
    assert store.agent_requires_2fa("persona_agent") is False
    store.assign_agent("persona_agent", enrol["profile_id"])
    assert store.agent_requires_2fa("persona_agent") is True
    assert store.verify_for_agent("persona_agent", _code_for(enrol)) is True
    assert store.verify_for_agent("persona_agent", "000000") is False
    # Unassigned agents never require/verify.
    assert store.verify_for_agent("notes_agent", _code_for(enrol)) is False


def test_assign_unknown_profile_rejected(store):
    with pytest.raises(SecurityProfileError):
        store.assign_agent("persona_agent", "does-not-exist")


def test_one_profile_assignable_to_multiple_agents(store):
    enrol = store.create_profile("Shared 2FA")
    store.assign_agent("persona_agent", enrol["profile_id"])
    store.assign_agent("goal_agent", enrol["profile_id"])
    meta = store.list_profiles()[0]
    assert set(meta["assigned_agents"]) == {"persona_agent", "goal_agent"}


def test_wipe_agent_clears_assignment_and_returns_profile(store):
    enrol = store.create_profile("Persona 2FA")
    store.assign_agent("persona_agent", enrol["profile_id"])
    freed = store.wipe_agent("persona_agent")
    assert freed == enrol["profile_id"]
    assert store.agent_requires_2fa("persona_agent") is False
    # The profile itself still exists (could be shared); wipe it explicitly.
    assert store.profile_exists(enrol["profile_id"]) is True
    assert store.wipe_profile(enrol["profile_id"]) is True
    assert store.profile_exists(enrol["profile_id"]) is False


def test_wipe_profile_removes_all_assignments(store):
    enrol = store.create_profile("Shared 2FA")
    store.assign_agent("persona_agent", enrol["profile_id"])
    store.assign_agent("goal_agent", enrol["profile_id"])
    assert store.wipe_profile(enrol["profile_id"]) is True
    assert store.agent_requires_2fa("persona_agent") is False
    assert store.agent_requires_2fa("goal_agent") is False
    assert store.list_profiles() == []


def test_reassign_agent_overwrites_profile(store):
    a = store.create_profile("Profile A")
    b = store.create_profile("Profile B")
    store.assign_agent("persona_agent", a["profile_id"])
    store.assign_agent("persona_agent", b["profile_id"])
    assert store.get_agent_profile_id("persona_agent") == b["profile_id"]
    assert store.verify_for_agent("persona_agent", _code_for(b)) is True
    assert store.verify_for_agent("persona_agent", _code_for(a)) is False

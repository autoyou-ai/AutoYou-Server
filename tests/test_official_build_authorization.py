import pytest

from scripts import check_official_build_authorization as gate


def _valid_payload() -> dict[str, object]:
    return {
        "authorized": True,
        "status": "authorized",
        "artifact_profile": "autoyou-server-windows-default",
        "agreement_id": "agreement-123",
        "recipients": ["build@autoyou.me", "release@example.com"],
        "oauth_email": "release@example.com",
    }


def test_authorization_payload_accepts_matching_signed_payload() -> None:
    gate._validate_authorization(
        _valid_payload(),
        "autoyou-server-windows-default",
        required_recipients=("build@autoyou.me",),
        expected_oauth_email="release@example.com",
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"authorized": False}), "not signed/authorized"),
        (lambda payload: payload.update({"status": "draft"}), "status is not authorized"),
        (lambda payload: payload.update({"artifact_profile": "other-profile"}), "artifact profile mismatch"),
        (lambda payload: payload.pop("agreement_id"), "missing the signed agreement id"),
        (lambda payload: payload.update({"recipients": ["release@example.com"]}), "missing required agreement recipient"),
        (lambda payload: payload.pop("oauth_email"), "missing the OAuth signer email"),
        (lambda payload: payload.update({"oauth_email": "other@example.com"}), "signer mismatch"),
    ],
)
def test_authorization_payload_rejects_invalid_signed_payloads(mutation, message: str) -> None:
    payload = _valid_payload()
    mutation(payload)

    with pytest.raises(RuntimeError, match=message):
        gate._validate_authorization(
            payload,
            "autoyou-server-windows-default",
            required_recipients=("build@autoyou.me",),
            expected_oauth_email="release@example.com",
        )


def test_required_mode_fails_closed_without_authorization(monkeypatch, capsys) -> None:
    monkeypatch.setattr(gate, "_discover_authorization_file", lambda _profile: None)

    exit_code = gate.main(["--required", "--artifact-profile", "autoyou-server-windows-default"])

    assert exit_code == 1
    assert "authorization is required" in capsys.readouterr().err

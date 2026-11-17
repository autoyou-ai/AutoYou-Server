import datetime

import pyotp

import server


def test_totp_dst_fallback_uses_only_utc(monkeypatch):
    secret = "JBSWY3DPEHPK3PXP"
    # Known ambiguous time from 2026-11-01 01:34:48 PDT (epoch 1793522088)
    utc_now = datetime.datetime.fromtimestamp(1793522088, datetime.timezone.utc)
    monkeypatch.setattr(server, "_totp_utc_now", lambda: utc_now)
    totp = pyotp.TOTP(secret)

    verification_times = []
    verify = pyotp.TOTP.verify

    def record_verification_time(self, code, for_time=None, valid_window=0):
        verification_times.append(for_time)
        return verify(self, code, for_time=for_time, valid_window=valid_window)

    monkeypatch.setattr(pyotp.TOTP, "verify", record_verification_time)

    current_code = totp.at(utc_now)
    shifted_code = totp.at(utc_now + datetime.timedelta(hours=1))
    assert server._totp_now(secret) == current_code
    assert server._verify_totp_secret(secret, current_code)

    # Simulate the extra counter the old naive fallback could accept on Windows.
    assert not server._verify_totp_secret(secret, shifted_code)
    assert verification_times == [utc_now, utc_now]

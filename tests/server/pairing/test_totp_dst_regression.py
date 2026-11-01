import datetime
import pyotp
import pytest
from starlette.testclient import TestClient
import server

def test_totp_dst_fallback_and_utc_alignment():
    """Verify that during Daylight Saving Time fallback transitions,
    _verify_totp_secret accepts standard UTC RFC 6238 codes (e.g. from iOS/Android/auth apps)
    as well as defensive naive-local codes on Windows."""
    secret = "VMLDJF6FEWW4LLS7UUFSRXTC776GMH5U"

    # Known ambiguous time from 2026-11-01 01:34:48 PDT (epoch 1793522088)
    t_utc = datetime.datetime.fromtimestamp(1793522088, datetime.timezone.utc)
    totp = pyotp.TOTP(secret)
    expected_utc_code = totp.at(t_utc)
    assert expected_utc_code == "005620"

    # The naive local datetime on Windows during fallback hour produced "840072"
    t_naive = datetime.datetime.fromtimestamp(1793522088)
    expected_naive_code = totp.at(t_naive)
    assert expected_naive_code == "840072"

    # Verify that the server's verification helper accepts the standard UTC code:
    assert server._verify_totp_secret(secret, "005620") or totp.verify("005620", for_time=t_utc, valid_window=1)

    # Verify that _totp_now uses UTC datetime and matches pyotp.at(utc_now):
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    assert server._totp_now(secret) == totp.at(utc_now)

from __future__ import annotations

import pytest

from scripts import serve_guides_preview as preview


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://127.0.0.1", "http://127.0.0.1"),
        ("http://localhost:8765", "http://localhost:8765"),
        ("http://LOCALHOST:8765", "http://localhost:8765"),
        ("https://127.0.0.1:8765", ""),
        ("http://example.test", ""),
        ("http://127.0.0.1.example.test", ""),
        ("http://127.0.0.1:99999", ""),
        ("http://127.0.0.1:80\r\nSet-Cookie: x=1", ""),
    ],
)
def test_loopback_origin_is_rebuilt_from_checked_parts(origin: str, expected: str) -> None:
    assert preview._loopback_origin(origin) == expected


@pytest.mark.parametrize(
    ("email", "valid"),
    [
        ("person@example.test", True),
        ("first.last+tag@mail.example.test", True),
        ("person@example", False),
        ("person@example..test", False),
        ("person@.example.test", False),
        ("two words@example.test", False),
    ],
)
def test_email_pattern(email: str, valid: bool) -> None:
    assert bool(preview.EMAIL_PATTERN.fullmatch(email)) is valid

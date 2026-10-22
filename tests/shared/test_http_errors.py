from __future__ import annotations

import logging

import httpx
import pytest

from shared.http_errors import connection_error_text, internal_error_message


def test_internal_error_message_logs_the_exception_under_a_reference(caplog) -> None:
    exc = OSError("[Errno 13] Permission denied: '/Users/example/Library/AutoYou/config.json'")
    with caplog.at_level(logging.ERROR, logger="autoyou.http_errors"):
        message = internal_error_message(exc)

    assert "Permission denied" not in message
    assert "/Users/example" not in message
    reference = message.split("(ref ", 1)[1].split(")", 1)[0]
    assert len(reference) == 8
    record = caplog.records[-1]
    assert reference in record.getMessage()
    assert "test_internal_error_message_logs_the_exception_under_a_reference" in record.getMessage()
    assert record.exc_info[1] is exc


def test_internal_error_message_uses_the_given_context_and_level(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="autoyou.http_errors"):
        internal_error_message(RuntimeError("boom"), context="Agent reload", level=logging.WARNING)
    assert caplog.records[-1].levelno == logging.WARNING
    assert caplog.records[-1].getMessage().startswith("Agent reload failed (ref ")


def test_each_call_gets_a_new_reference() -> None:
    assert internal_error_message(ValueError("x")) != internal_error_message(ValueError("x"))


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("[Errno 61] Connection refused"), "Connection refused"),
        (httpx.ReadTimeout("timed out"), "Timed out"),
        (
            httpx.HTTPStatusError(
                "401 Unauthorized for url 'http://127.0.0.1:18789/v1/models'",
                request=httpx.Request("GET", "http://127.0.0.1:18789/v1/models"),
                response=httpx.Response(401),
            ),
            "HTTP 401",
        ),
        (ConnectionRefusedError("refused"), "Connection refused"),
        (TimeoutError(), "Timed out"),
        (ValueError("bad json at /Users/example"), "Not reachable"),
    ],
)
def test_connection_error_text_names_the_failure_without_its_message(exc: BaseException, expected: str) -> None:
    assert connection_error_text(exc) == expected

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import logging

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from shared.request_logging import install_route_aware_request_logging


@pytest.mark.parametrize(
    "method,path,status,level",
    [
        ("GET", "/api/screen-listen", 200, logging.DEBUG),
        ("GET", "/api/screen-listen", 403, logging.INFO),
        ("POST", "/api/screen-listen", 200, logging.INFO),
        ("GET", "/api/other", 200, logging.INFO),
    ],
)
def test_status_polls_are_quiet_but_errors_and_changes_remain_visible(caplog, method, path, status, level):
    app = FastAPI()
    logger_name = "autoyou.test.request_logging"
    install_route_aware_request_logging(
        app, logger_name=logger_name, debug_path_prefixes=("/api/screen-listen",)
    )

    @app.api_route(path, methods=[method])
    def endpoint():
        return Response(status_code=status)

    with caplog.at_level(logging.DEBUG, logger=logger_name), TestClient(app) as client:
        assert client.request(method, path).status_code == status

    records = [record for record in caplog.records if record.name == logger_name]
    assert len(records) == 1
    assert records[0].levelno == level
    assert f'"{method} {path} HTTP/1.1" {status}' in records[0].getMessage()

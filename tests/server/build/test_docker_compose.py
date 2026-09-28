# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import yaml

from tests.support.paths import REPO_ROOT


def test_default_compose_keeps_services_on_loopback_and_uses_named_volumes():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    named_volumes = set(compose["volumes"])
    autoyou = compose["services"]["autoyou"]
    assert any(value.startswith("AUTOYOU_SERVER_PASSWORD=${AUTOYOU_SERVER_PASSWORD:?")
               for value in autoyou["environment"])
    assert autoyou["build"]["args"]["AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD"] == "${AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD:-0}"
    assert "AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=${AUTOYOU_NO_DOWNLOAD_TUNNELMOLE:-0}" in autoyou["environment"]

    for service in compose["services"].values():
        assert all(isinstance(port, str) and port.startswith("127.0.0.1:") for port in service.get("ports", []))
        assert all(volume.split(":", 1)[0] in named_volumes for volume in service.get("volumes", []))

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-bcfc94b7eeda7f52b9001a9d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-bcfc94b7eeda7f52b9001a9d"

from pathlib import Path

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server


def test_license_route_finds_packaged_legal_bundle(monkeypatch, tmp_path):
    backend_dir = tmp_path / "Backend"
    legal_dir = backend_dir / "Legal"
    legal_dir.mkdir(parents=True)
    license_text = "Synthetic packaged license\n"
    (legal_dir / "LICENSE").write_text(license_text, encoding="utf-8")

    monkeypatch.setattr(server, "APP_ROOT", tmp_path)
    monkeypatch.setattr(server, "RESOURCES_ROOT", tmp_path)
    monkeypatch.setattr(server, "__file__", str(backend_dir / "server.py"))
    monkeypatch.setattr(server.sys, "executable", str(backend_dir / "AutoYou.exe"))
    monkeypatch.setenv("AUTOYOU_PACKAGED_RESOURCES_ROOT", str(tmp_path))

    client = TestClient(server.admin_app)
    response = client.get("/LICENSE")

    assert response.status_code == 200
    assert response.text == license_text


def test_license_candidate_paths_include_release_legal_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "APP_ROOT", tmp_path)
    monkeypatch.setattr(server, "RESOURCES_ROOT", tmp_path)
    monkeypatch.setattr(server, "__file__", str(tmp_path / "Backend" / "server.py"))
    monkeypatch.setattr(server.sys, "executable", str(tmp_path / "Backend" / "AutoYou.exe"))
    monkeypatch.setenv("AUTOYOU_PACKAGED_RESOURCES_ROOT", str(tmp_path))

    candidates = {Path(path) for path in server._candidate_legal_file_paths("LICENSE")}

    assert tmp_path / "Legal" / "LICENSE" in candidates
    assert tmp_path / "Backend" / "Legal" / "LICENSE" in candidates


def test_source_mode_serves_generated_notice_bundle():
    client = TestClient(server.admin_app)

    for route in ("/NOTICE.txt", "/THIRD-PARTY-NOTICES.md", "/sbom.cdx.json"):
        response = client.get(route)

        assert response.status_code == 200
        assert response.text


def test_license_route_finds_outer_macos_app_legal_bundle(monkeypatch, tmp_path):
    app_resources = tmp_path / "AutoYou.app" / "Contents" / "Resources"
    backend_root = app_resources / "backend" / "AutoYou.dist"
    legal_dir = app_resources / "Legal"
    backend_root.mkdir(parents=True)
    legal_dir.mkdir(parents=True)
    license_text = "Synthetic macOS app legal license\n"
    (legal_dir / "LICENSE").write_text(license_text, encoding="utf-8")

    monkeypatch.setattr(server, "APP_ROOT", backend_root)
    monkeypatch.setattr(server, "RESOURCES_ROOT", backend_root)
    monkeypatch.setattr(server, "__file__", str(backend_root / "server.py"))
    monkeypatch.setattr(server.sys, "executable", str(backend_root / "AutoYouServer"))
    monkeypatch.setenv("AUTOYOU_PACKAGED_RESOURCES_ROOT", str(backend_root))

    client = TestClient(server.admin_app)
    response = client.get("/LICENSE")

    assert response.status_code == 200
    assert response.text == license_text

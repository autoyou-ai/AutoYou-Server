"""Hermetic checks for the non-installing manifest advisory helper."""

import importlib.util
from pathlib import Path
import urllib.error


SPEC = importlib.util.spec_from_file_location(
    "dependency_advisories", Path(__file__).resolve().parents[1] / "scripts/audit_dependency_advisories.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_inventory_distinguishes_pins_minimums_and_unsupported_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    manifest = tmp_path / "profile.txt"
    manifest.write_text(
        "Example_Package[extra]==1.2.3; python_version >= '3.10'\n"
        "example-package==1.2.3\n"
        "floor-package>=2.0,<3\n"
        "upper-only<2\n"
        "git+https://example.invalid/repo.git@0123456789\n"
        "    --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    records, skipped = audit.inventory([manifest], include_minimums=True)
    assert [(item["name"], item["version"]) for item in records] == [
        ("example-package", "1.2.3"), ("floor-package", "2.0")
    ]
    assert len(records[0]["sources"]) == 2
    assert records[1]["sources"][0]["kind"] == "minimum"
    assert len(skipped) == 2
    pins, skipped = audit.inventory([manifest], include_minimums=False)
    assert len(pins) == 1
    assert len(skipped) == 3


def test_lookup_errors_are_not_clean_results(monkeypatch):
    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("synthetic unavailable service")

    monkeypatch.setattr(audit.urllib.request, "urlopen", unavailable)
    result = audit.query_release({"name": "example-package", "version": "1.0", "sources": []})
    assert result["status"] == "error"
    assert "advisories" not in result


def test_lookup_filters_withdrawn_advisories(monkeypatch):
    import io

    payload = b'{"info":{"yanked":false},"vulnerabilities":[{"id":"TEST-1","withdrawn":null},{"id":"TEST-2","withdrawn":"2026-01-01"}]}'
    monkeypatch.setattr(audit.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(payload))
    result = audit.query_release({"name": "example-package", "version": "1.0", "sources": []})
    assert result["status"] == "checked"
    assert [entry["id"] for entry in result["advisories"]] == ["TEST-1"]


def test_missing_advisory_fields_are_not_clean_results(monkeypatch):
    import io

    monkeypatch.setattr(audit.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b'{}'))
    result = audit.query_release({"name": "example-package", "version": "1.0", "sources": []})
    assert result["status"] == "error"
    assert "advisories" not in result

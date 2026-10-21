# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-C-20616e642061747472696275-c866aa96800e89bbaf567a10

"""Hermetic checks for the non-installing manifest advisory helper."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"
# from __debug_trace_c__ import autoyou_provenance_c_20616e642061747472696275_c866aa96800e89bbaf567a10

import importlib.util
from pathlib import Path
import urllib.error

__debug_trace_c__ = "AUTOYOU-PROVENANCE-C-20616e642061747472696275-c866aa96800e89bbaf567a10"


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


def _decision(**overrides):
    decision = {
        "package": "Example_Package", "versions": ["1.0"], "advisory_ids": ["TEST-ALIAS"],
        "manifests": ["requirements/tuning.txt"], "justification": "vulnerable_code_not_in_execute_path",
        "detail": "synthetic", "reviewed_at": "2026-01-01", "review_by": "2026-02-01",
    }
    decision.update(overrides)
    return decision


def _result(version="1.0", file="requirements/tuning.txt"):
    return {"name": "example-package", "version": version, "status": "checked",
            "sources": [{"file": file, "line": 1, "kind": "minimum"}],
            "advisories": [{"id": "TEST-1", "aliases": ["TEST-ALIAS"]}]}


def test_scoped_decision_moves_advisory_to_reviewed_until_it_expires():
    result = _result()
    audit.apply_decisions([result], [_decision()], "2026-01-15")
    assert result["advisories"] == []
    assert result["reviewed_advisories"][0]["decision"]["review_by"] == "2026-02-01"

    expired = _result()
    audit.apply_decisions([expired], [_decision()], "2026-02-02")
    assert [item["id"] for item in expired["advisories"]] == ["TEST-1"]
    assert "reviewed_advisories" not in expired


def test_decisions_do_not_cover_other_versions_manifests_or_advisories():
    for result, decision in (
        (_result(version="2.0"), _decision()),
        (_result(file="requirements/base.txt"), _decision()),
        (_result(), _decision(advisory_ids=["OTHER-1"])),
    ):
        audit.apply_decisions([result], [decision], "2026-01-15")
        assert [item["id"] for item in result["advisories"]] == ["TEST-1"]


def test_incomplete_decision_file_is_rejected(tmp_path):
    path = tmp_path / "advisory-decisions.json"
    path.write_text('{"decisions": [{"package": "example-package"}]}', encoding="utf-8")
    try:
        audit.load_decisions(path)
    except ValueError:
        pass
    else:
        raise AssertionError("An incomplete decision must not be honored")
    assert audit.load_decisions(tmp_path / "missing.json") == []


def test_repository_decisions_are_complete_and_time_bounded():
    decisions = audit.load_decisions(audit.DECISIONS)
    assert decisions, "Expected the reviewed advisory decisions file"
    for decision in decisions:
        assert decision["review_by"] > decision["reviewed_at"]
        assert all((audit.REPO_ROOT / manifest).is_file() for manifest in decision["manifests"])


def test_missing_advisory_fields_are_not_clean_results(monkeypatch):
    import io

    monkeypatch.setattr(audit.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b'{}'))
    result = audit.query_release({"name": "example-package", "version": "1.0", "sources": []})
    assert result["status"] == "error"
    assert "advisories" not in result

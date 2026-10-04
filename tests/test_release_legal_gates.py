# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-9373534ec8f2ef341a029959

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import copy

from scripts import check_release_legal_gates as legal_gates
from scripts.generate_release_legal_artifacts import build_notice, build_sbom, expand_artifact_profiles, load_config

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-9373534ec8f2ef341a029959"


def test_public_legal_gate_defaults_to_a_server_check_with_an_ignored_report() -> None:
    args = legal_gates.build_parser().parse_args([])

    assert legal_gates.ARTIFACT_SCOPES == ("server",)
    assert args.artifact_scope == "server"
    assert not args.generate
    assert legal_gates.build_parser().parse_args(["--generate"]).generate
    assert not legal_gates.build_parser().parse_args(["--no-generate"]).generate
    assert args.report == legal_gates.DEFAULT_REPORT_PATH
    assert args.report.relative_to(legal_gates.REPO_ROOT).as_posix().startswith("build/")
    assert "/build/" in (legal_gates.REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_current_generated_server_bundles_are_fresh() -> None:
    report = legal_gates.GateReport()

    legal_gates.check_generated_bundles(load_config(), report)

    assert report.ok, report.failures


def test_manifest_inventories_describe_their_scope() -> None:
    profile = {"id": "synthetic-profile", "displayName": "Synthetic declaration inventory"}
    timestamp = "2026-10-04T00:00:00+00:00"
    sbom = build_sbom(profile, [], timestamp=timestamp)
    metadata = {item["name"]: item["value"] for item in sbom["metadata"]["properties"]}
    assert metadata["autoyou:inventoryScope"] == "manifest-and-manual-declarations"
    assert "Not a resolved environment" in metadata["autoyou:inventoryLimitations"]
    notice = build_notice(profile, [], timestamp=timestamp)
    assert "does not establish actual installed or bundled versions" in notice


def test_current_server_legal_gate_has_no_automatic_failures() -> None:
    report = legal_gates.run_gates(
        load_config(),
        generate=False,
        strict_unknown_license=True,
        allow_open_release_blockers=True,
    )

    assert report.ok, report.failures


def test_private_review_is_optional_only_for_contributor_checks(monkeypatch) -> None:
    monkeypatch.delenv("AUTOYOU_RELEASE_CHECKLIST", raising=False)
    metadata = legal_gates.GateReport()
    legal_gates.check_release_checklist_blockers(metadata, allow_open_release_blockers=True)
    assert metadata.ok
    assert metadata.warnings
    release = legal_gates.GateReport()
    legal_gates.check_release_checklist_blockers(release, allow_open_release_blockers=False)
    assert not release.ok


def test_private_review_stays_external_and_does_not_leak_details(tmp_path, monkeypatch) -> None:
    public_root = tmp_path / "public-server"
    public_root.mkdir()
    monkeypatch.setattr(legal_gates, "REPO_ROOT", public_root)
    review = tmp_path / "private-review.md"
    review.write_text("- [ ] Synthetic confidential reviewer note\n", encoding="utf-8")
    report = legal_gates.GateReport()
    legal_gates.check_release_checklist_blockers(
        report, allow_open_release_blockers=False, checklist_path=review
    )
    assert not report.ok
    assert "1 open item" in report.failures[0]
    assert "confidential" not in str(report.to_json())
    assert str(review) not in str(report.to_json())
    review.write_text("- [x] Synthetic completed review\n", encoding="utf-8")
    monkeypatch.setenv("AUTOYOU_RELEASE_CHECKLIST", str(review))
    complete = legal_gates.GateReport()
    legal_gates.check_release_checklist_blockers(complete, allow_open_release_blockers=False)
    assert complete.ok
    inside = public_root / "private-review.md"
    inside.write_text(review.read_text(encoding="utf-8"), encoding="utf-8")
    rejected = legal_gates.GateReport()
    legal_gates.check_release_checklist_blockers(
        rejected, allow_open_release_blockers=False, checklist_path=inside
    )
    assert not rejected.ok
    assert "outside" in rejected.failures[0]


def test_empty_private_review_cannot_approve_a_release(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(legal_gates, "REPO_ROOT", tmp_path / "public-server")
    review = tmp_path / "empty-review.md"
    review.write_text("No review decisions\n", encoding="utf-8")
    report = legal_gates.GateReport()
    legal_gates.check_release_checklist_blockers(
        report, allow_open_release_blockers=False, checklist_path=review
    )
    assert not report.ok


def test_source_profile_records_external_service_terms() -> None:
    config = load_config()
    source_profile = next(
        profile
        for profile in config["artifactProfiles"]
        if profile["id"] == "autoyou-server-source-full"
    )
    components = {item["name"]: item for item in source_profile["manualComponents"]}
    # from __debug_provenance_r__ import via

    expected = {
        "ElevenLabs API service terms",
        "Google AI and GenAI API terms",
    }

    assert expected <= components.keys()
    assert all(components[name]["source"].startswith("https://") for name in expected)


def test_emotivoice_vendor_is_included_only_in_voice_release_profiles() -> None:
    profiles = {profile["id"]: profile for profile in expand_artifact_profiles(load_config())}

    assert any(
        item["name"] == "EmotiVoice inference code"
        for item in profiles["autoyou-server-source-full"]["manualComponents"]
    )
    for profile_id in ("autoyou-server-windows-connector-full", "autoyou-server-macos-connector-full"):
        assert any(item["name"] == "EmotiVoice inference code" for item in profiles[profile_id]["manualComponents"])
    for profile_id in ("autoyou-server-windows-default", "autoyou-server-macos-default"):
        assert not any(item["name"] == "EmotiVoice inference code" for item in profiles[profile_id]["manualComponents"])


def test_generated_bundles_detect_profile_drift() -> None:
    config = copy.deepcopy(load_config())
    config["artifactProfiles"][0]["displayName"] = "Synthetic stale profile"
    report = legal_gates.GateReport()

    legal_gates.check_generated_bundles(config, report)

    assert any("Generated SBOM for autoyou-server-source-full is stale" in failure for failure in report.failures)
    assert any("Generated NOTICE for autoyou-server-source-full is stale" in failure for failure in report.failures)

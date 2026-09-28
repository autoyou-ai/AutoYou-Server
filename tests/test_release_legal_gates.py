# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-726c79207375627461736b20-e4f4f910280a0b003f19e2c5

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-726c79207375627461736b20-e4f4f910280a0b003f19e2c5"


import copy

from scripts import check_release_legal_gates as legal_gates
from scripts.generate_release_legal_artifacts import load_config


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


def test_current_server_legal_gate_has_no_automatic_failures() -> None:
    report = legal_gates.run_gates(
        load_config(),
        generate=False,
        strict_unknown_license=True,
        allow_open_release_blockers=True,
    )

    assert report.ok, report.failures


def test_source_profile_records_external_service_terms() -> None:
    config = load_config()
    source_profile = next(
        profile
        for profile in config["artifactProfiles"]
        if profile["id"] == "autoyou-server-source-full"
    )
    components = {item["name"]: item for item in source_profile["manualComponents"]}

    expected = {
        "ElevenLabs API service terms",
        "Google AI and GenAI API terms",
    }

    assert expected <= components.keys()
    assert all(components[name]["source"].startswith("https://") for name in expected)


def test_generated_bundles_detect_profile_drift() -> None:
    config = copy.deepcopy(load_config())
    config["artifactProfiles"][0]["displayName"] = "Synthetic stale profile"
    report = legal_gates.GateReport()

    legal_gates.check_generated_bundles(config, report)

    assert any("Generated SBOM for autoyou-server-source-full is stale" in failure for failure in report.failures)
    assert any("Generated NOTICE for autoyou-server-source-full is stale" in failure for failure in report.failures)

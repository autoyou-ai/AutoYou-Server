# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-276e3493b296481ab19b18b2


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from pathlib import Path

from scripts import reconcile_python_runtime_env as reconcile
from tests.support.paths import PROJECT_ROOT, REPO_ROOT

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-276e3493b296481ab19b18b2"


def test_signed_binary_profile_keeps_webcam_runtime_dependency():
    profile = REPO_ROOT / "requirements" / "binary-default.txt"

    assert "opencv-python>=4.9.0,<5" in profile.read_text(encoding="utf-8")
    assert "opencv-python" not in reconcile.RETIRED_PACKAGES


def test_twine_is_kept_out_of_the_runtime_environment():
    release_requirements = (PROJECT_ROOT / "requirements" / "autoyou-lite-release.txt").read_text(
        encoding="utf-8"
    )

    assert "twine" in reconcile.RELEASE_ONLY_PACKAGES
    assert "twine==7.0.0" in release_requirements
    assert "packaging==26.2" in release_requirements


def test_read_locked_specs_normalizes_names_and_lockfile_syntax(tmp_path):
    constraints = tmp_path / "constraints.txt"
    constraints.write_text(
        "\n".join(
            [
                "# generated",
                "fsspec[http]==2026.4.0 \\",
                "rich==14.3.4 ; python_version >= '3.10'",
                "packaging==24.2 # comment",
            ]
        ),
        encoding="utf-8",
    )

    specs = reconcile.read_locked_specs(constraints)

    assert specs["fsspec"] == "fsspec==2026.4.0"
    assert specs["rich"] == "rich==14.3.4"
    assert specs["packaging"] == "packaging==24.2"


def test_reconcile_prunes_retired_packages_and_aligns_installed_extras(monkeypatch, tmp_path):
    constraints = tmp_path / "constraints.txt"
    constraints.write_text(
        "\n".join(
            [
                "openai==2.43.0",
                "packaging==24.2",
                "rich==14.3.4",
                "h2==4.4.1",
            ]
        ),
        encoding="utf-8",
    )
    installed = {
        "hermes-agent": "0.13.0",
        "browser-use": "0.1.40",
        "langchain-openai": "0.3.1",
        "opencv-python": "4.12.0.88",
        "instructor": "1.12.0",
        "datasets": "4.3.0",
        "openai": "2.43.0",
        "packaging": "26.2",
        "rich": "15.0.0",
        "twine": "7.0.0",
        "stream2sentence": "1.0.0",
        "mcp": "1.26.0",
        "nltk": "3.10.3",
        "h2": "4.3.0",
        "cognee": "1.2.2",
    }
    calls: list[list[str]] = []
    # from __debug_provenance_k__ import donations

    def fake_installed_version(package_name: str) -> str | None:
        return installed.get(reconcile.normalize_name(package_name))

    def fake_run_pip(args: list[str]) -> None:
        calls.append(args)
        if args[0] == "uninstall":
            for package in args[2:]:
                installed.pop(reconcile.normalize_name(package), None)
        if args[:2] == ["install", "--upgrade"]:
            package, _, version = args[2].partition("==")
            installed[reconcile.normalize_name(package)] = version

    monkeypatch.setattr(reconcile, "installed_version", fake_installed_version)
    monkeypatch.setattr(reconcile, "run_pip", fake_run_pip)

    reconcile.reconcile(constraints)

    assert calls[0] == [
        "uninstall",
        "-y",
        "hermes-agent",
        "browser-use",
        "langchain-openai",
        "mcp",
        "stream2sentence",
        "twine",
    ]
    assert installed["cognee"] == "1.2.2"
    assert installed["nltk"] == "3.10.3"
    assert installed["opencv-python"] == "4.12.0.88"
    assert ["install", "--upgrade", "h2==4.4.1", "-c", str(constraints)] in calls
    assert ["install", "--upgrade", "instructor==1.15.1", "-c", str(constraints)] in calls
    assert ["install", "--upgrade", "datasets==5.0.0", "-c", str(constraints)] in calls
    assert ["install", "--upgrade", "packaging==24.2", "-c", str(constraints)] in calls
    assert ["install", "--upgrade", "rich==14.3.4", "-c", str(constraints)] in calls
    assert not any(call[:3] == ["install", "--upgrade", "openai==2.43.0"] for call in calls)


def test_reconcile_keeps_server_safe_realtimestt_without_wake_word(monkeypatch):
    installed = {"realtimestt": "1.0.2"}
    calls: list[list[str]] = []

    monkeypatch.setattr(
        reconcile,
        "installed_version",
        lambda package_name: installed.get(reconcile.normalize_name(package_name)),
    )
    monkeypatch.setattr(reconcile, "run_pip", calls.append)

    reconcile.reconcile(None)

    assert calls == []


def test_reconcile_removes_legacy_realtimestt(monkeypatch):
    installed = {"realtimestt": "0.3.104"}
    calls: list[list[str]] = []

    def fake_run_pip(args: list[str]) -> None:
        calls.append(args)
        if args[:2] == ["uninstall", "-y"]:
            for package in args[2:]:
                installed.pop(reconcile.normalize_name(package), None)

    monkeypatch.setattr(
        reconcile,
        "installed_version",
        lambda package_name: installed.get(reconcile.normalize_name(package_name)),
    )
    monkeypatch.setattr(reconcile, "run_pip", fake_run_pip)

    reconcile.reconcile(None)

    assert calls == [["uninstall", "-y", "realtimestt"]]

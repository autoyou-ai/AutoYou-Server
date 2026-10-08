# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-d4d75332081125a1482f8714


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import re

import pytest

from scripts import export_public_autoyou_server as exporter

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-d4d75332081125a1482f8714"


def test_force_export_preserves_a_nested_git_checkout(tmp_path) -> None:
    output = tmp_path / "AutoYou-Server"
    marker = output / ".git" / "HEAD"
    marker.parent.mkdir(parents=True)
    marker.write_text("synthetic", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Refusing to replace a Git checkout"):
        exporter.export_entries([], output, force=True)
    assert marker.read_text(encoding="utf-8") == "synthetic"


def test_public_checkout_uses_its_own_readme_and_excludes_private_review(monkeypatch) -> None:
    monkeypatch.setattr(exporter.subprocess, "check_output", lambda *args, **kwargs: b"public source")
    entries = [
        exporter.GitEntry(path, "100644", "blob", "0" * 40)
        for path in ("README.md", "docs/legal/release-compliance-checklist.md")
    ]

    included, failures = exporter.build_export_plan(entries)
    assert failures == []
    assert [entry.path for entry in included] == ["README.md"]


def test_public_export_preserves_public_dotfiles() -> None:
    assert exporter.normalize_path(".gitignore") == ".gitignore"
    assert exporter.should_publish_path(".gitignore")
    assert exporter.should_publish_path(".gitleaks.toml")
    assert exporter.should_publish_path("CONTRIBUTING.md")
    assert exporter.should_publish_path(".github/workflows/public-checks.yml")
    assert exporter.audit_public_source_paths(sorted(exporter.PUBLIC_GITHUB_PATHS)) == []
    assert exporter.audit_public_source_paths(sorted(exporter.PUBLIC_SKILL_PATHS)) == []
    assert exporter.should_publish_path("vendor/INSTRUCTIONS.md")
    assert exporter.should_publish_path("config/donations.example.json")
    assert not exporter.should_publish_path("config/donations.json")
    assert exporter.should_publish_path(".agents/skills/autoyou-server-validate/SKILL.md")
    assert not exporter.should_publish_path(".agents/skills/unreviewed/SKILL.md")
    assert exporter.should_publish_path("docs/contributors/AGENTS.md")
    assert exporter.should_publish_path("docs/contributors/CLA.md")
    assert exporter.should_publish_path("docs/contributors/contributor-pool.md")
    assert exporter.should_publish_path(".github/FUNDING.yml")
    assert exporter.should_publish_path(".github/pull_request_template.md")
    assert not exporter.should_publish_path("docs/contributors/unreviewed.md")
    assert exporter.should_publish_path("docs/contributors/CLAUDE.md")
    assert exporter.should_publish_path("docs/contributors/CODEX.md")
    assert exporter.should_publish_path("docs/contributors/LLM.txt")
    assert exporter.should_publish_path("docs/contributors/README.md")
    assert exporter.should_publish_path("docs/contributors/TESTING.md")
    assert exporter.should_publish_path("docs/contributors/TESTING_SKILL.md")
    assert exporter.should_publish_path("docs/contributors/TEST_RUNNER_AGENT.md")
    assert exporter.should_publish_path("server.py")
    assert exporter.should_publish_path("core_server/app.py")
    assert exporter.should_publish_path("routers/admin.py")
    assert exporter.should_publish_path("scripts/verify_windows_server_msix.py")
    assert exporter.should_publish_path("docs/legal/generated/README.md")
    assert exporter.should_publish_path("docs/legal/generated/manifest-summary.json")
    assert exporter.should_publish_path("docs/legal/messaging-partner-policy.md")
    assert exporter.should_publish_path("docs/legal/open-source-commitment.md")
    assert exporter.should_publish_path("docs/legal/optional-integrations.md")
    assert all(exporter.should_publish_path(path) for path in (
        "docs/images/admin/overview.png",
        "docs/images/admin/live-view.png",
        "docs/images/admin/security.png",
    ))


def test_secret_scan_type_annotation_exceptions_work_with_windows_line_endings() -> None:
    config = (exporter.REPO_ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    patterns = [re.compile(value) for value in re.findall(r"'''(.*?)'''", config)]
    annotation_type = "x25519.X25519PrivateKey"
    for suffix in ("", "\r"):
        assert any(pattern.fullmatch(f"    private_key: {annotation_type}{suffix}") for pattern in patterns)
        assert any(pattern.fullmatch("    password_key: bytes" + suffix) for pattern in patterns)
        assert not any(pattern.fullmatch('    private_key: "<synthetic-secret>"' + suffix) for pattern in patterns)


def test_public_export_includes_reviewed_vendor_and_runtime_files() -> None:
    assert all(exporter.should_publish_path(path) for path in (
        "ai.txt",
        "robots.txt",
        "vendor/emotivoice/LICENSE",
        "vendor/emotivoice/frontend.py",
        "vendor/emotivoice/models/hifigan/models.py",
        "autoyou_agents/page_agent/website/frontend/assets/autoyou-mark.svg",
    ))
    # The vendored prefix must not bypass the reviewed-asset gate or admit other checkouts.
    assert not exporter.should_publish_path("vendor/emotivoice/demo/sample.wav")
    assert not exporter.should_publish_path("vendor/other/module.py")
    assert not exporter.should_publish_path("autoyou_agents/page_agent/website/frontend/assets/unreviewed.svg")


def test_public_export_excludes_private_release_material() -> None:
    private_paths = (
        "docs/legal/release-compliance-checklist.md",
        "docs/legal/member-server-compliance-checklist.md",
        "docs/legal/maintainer-release-record.md",
        "AGENTS.md",
        "CLAUDE.md",
        "llm.txt",
        "guides/llm.txt",
        "docs/contributors/legacy/llm.txt",
        "shared/some_agent/pending_ad_credits.json",
        "docs/cloud/overview.md",
        "docs/user/chat.md",
        "private/release-notes.md",
        "scratch/plan.md",
        "tests/agents/private/test_custom_agent.py",
        "autoyou_agents/private/custom_agent/agent.py",
        # Only this repository's registered built-in agents are published.
        "autoyou_agents/unregistered_agent/agent.py",
    )

    assert all(not exporter.should_publish_path(path) for path in private_paths)
    assert not exporter.should_publish_path("vendor/cognee/README.md")


def test_this_repository_names_no_private_paths_of_its_own() -> None:
    # A checkout that holds this repository can add its own names; this one adds none.
    assert exporter.EXTRA_PRIVATE_PREFIXES == ()
    assert exporter.EXTRA_PRIVATE_TEST_PREFIXES == ()
    assert exporter.EXTRA_PRIVATE_EXACT_PATHS == set()
    assert exporter.PRIVATE_EXACT_PATHS == set()


def test_extra_private_paths_extend_the_boundary(monkeypatch) -> None:
    monkeypatch.setattr(exporter, "EXTRA_PRIVATE_PREFIXES", ("scripts/held_back/",))
    monkeypatch.setattr(exporter, "EXTRA_PRIVATE_TEST_PREFIXES", ("tests/held_back/",))
    monkeypatch.setattr(exporter, "EXTRA_PRIVATE_EXACT_PATHS", {"scripts/held_back_tool.py"})

    for path in ("scripts/held_back/tool.py", "tests/held_back/test_tool.py", "scripts/held_back_tool.py"):
        assert not exporter.should_publish_path(path)
    assert exporter.should_publish_path("scripts/verify_windows_server_msix.py")


def test_public_export_excludes_obsolete_unmaintained_docs() -> None:
    assert not exporter.should_publish_path("docs/agents/overview.md")
    assert not exporter.should_publish_path("docs/api/chat.md")


def test_public_export_rejects_runtime_and_unreviewed_artifacts() -> None:
    private_paths = (
        "scripts/__pycache__/export_public_autoyou_server.cpython-313.pyc",
        "shared/agent_install_registry.json",
        "shared/output/runtime.log",
        "node/whatsapp/.wwebjs_auth/session/Cookies",
        "servers/windows/dist/release.zip",
        "docs/images/private-screenshot.png",
        "docs/images/admin/unreviewed.png",
        "shared/native/unknown.dll",
        "shared/native/unknown.pyd",
    )

    assert exporter.should_publish_path("shared/native/libsodium/windows-x86_64/libsodium.dll")
    assert all(not exporter.should_publish_path(path) for path in private_paths)
    assert exporter.audit_public_repository_paths(private_paths) == [
        f"sensitive path committed to public repository: {path}" for path in private_paths
    ]


def test_public_export_includes_desktop_setup_contract_but_excludes_user_assets() -> None:
    private_paths = (
        "autoyou_agents/claude_desktop_agent/desktop_assets/windows/input_box.png",
        "autoyou_agents/claude_desktop_agent/desktop_assets/windows/1.14271/sprites/copy_response.png",
        "autoyou_agents/codex_desktop_agent/desktop_assets/tools/capture_sprites.py",
        "autoyou_agents/codex_desktop_agent/desktop_assets/macos/26.616/README.md",
    )
    # from __debug_provenance_q__ import payment

    assert all(exporter.should_publish_path(path) for path in exporter.PUBLIC_DESKTOP_ASSET_PATHS)
    assert not exporter.should_publish_path("autoyou_agents/private/custom_agent/desktop_assets/manifest.json")
    assert not exporter.should_publish_path("autoyou_agents/codex_desktop_agent/desktop_assets/manifest.json")
    assert not exporter.should_publish_path("autoyou_agents/codex_desktop_agent/desktop_assets/preferences.json")
    assert exporter.audit_public_source_paths(sorted(exporter.PUBLIC_DESKTOP_ASSET_PATHS)) == []
    assert all(not exporter.should_publish_path(path) for path in private_paths)
    assert exporter.audit_public_repository_paths(private_paths) == [
        f"desktop capture asset committed to public repository: {path}" for path in private_paths
    ]


def test_public_repository_boundary_rejects_nonpublic_committed_paths() -> None:
    failures = exporter.audit_public_repository_paths(
        (
            "server.py",
            "notes/private-plan.md",
            "private/agent.py",
            ".env",
            "tests/agents/private/test_custom_agent.py",
            "autoyou_agents/unregistered_agent/agent.py",
            "unreviewed-archive.zip",
        )
    )

    assert failures == [
        "path outside the public allowlist committed to public repository: notes/private-plan.md",
        "private path committed to public repository: private/agent.py",
        "sensitive path committed to public repository: .env",
        "private path committed to public repository: tests/agents/private/test_custom_agent.py",
        "unregistered agent package committed to public repository: autoyou_agents/unregistered_agent/agent.py",
        "sensitive path committed to public repository: unreviewed-archive.zip",
    ]


def test_worktree_check_uses_present_files_and_scans_content(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(exporter, "REPO_ROOT", tmp_path)
    (tmp_path / "server.py").write_text("print('safe')\n", encoding="utf-8")
    monkeypatch.setattr(
        exporter.subprocess,
        "check_output",
        lambda *_args, **_kwargs: b"server.py\0deleted.py\0",
    )

    included, failures = exporter.build_worktree_check_plan()

    assert included == ["server.py"]
    assert failures == []

    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    (tmp_path / "server.py").write_text(f"{private_key_marker}\n", encoding="utf-8")
    _included, failures = exporter.build_worktree_check_plan()

    assert failures == ["sensitive content included: server.py (private key PEM)"]


def test_worktree_check_requires_check_mode() -> None:
    try:
        exporter.main(["--worktree"])
    except SystemExit as exc:
        assert str(exc) == "--worktree requires --check"
    else:
        raise AssertionError("--worktree must require --check")


def test_worktree_check_rejects_ref_and_unreadable_files(monkeypatch) -> None:
    try:
        exporter.main(["--worktree", "--check", "--ref", "main"])
    except SystemExit as exc:
        assert str(exc) == "--worktree cannot be combined with --ref"
    else:
        raise AssertionError("--worktree must not use a committed ref")

    monkeypatch.setattr(exporter, "worktree_paths", lambda: ["server.py"])
    monkeypatch.setattr(exporter, "worktree_file_bytes", lambda _path: (_ for _ in ()).throw(OSError("synthetic read error")))

    _included, failures = exporter.build_worktree_check_plan()

    assert failures == ["unable to read worktree file: server.py (synthetic read error)"]

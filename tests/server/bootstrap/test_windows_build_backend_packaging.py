# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_BACKEND = PROJECT_ROOT / "servers" / "windows" / "build-backend.ps1"


def _build_backend_text() -> str:
    return BUILD_BACKEND.read_text(encoding="utf-8")


def _private_source_text(relative_path: str) -> str:
    for root in (PROJECT_ROOT, PROJECT_ROOT.parent):
        path = root / relative_path
        if path.is_file():
            return path.read_text(encoding="utf-8")
    pytest.skip(f"Private source checkout is absent: {relative_path}")


def test_windows_runtime_site_packages_are_pruned_to_active_requirements():
    text = _build_backend_text()

    assert "scripts\\prune_runtime_site_packages_to_requirements.py" in text
    assert 'Remove-DirectoryPatternsRecursively -Root $runtimeSitePackagesRoot -Patterns @("~*")' in text
    copy_site_packages = text.index("Copy-DirectoryContents -Source $sitePackagesRoot -Destination $runtimeSitePackagesRoot")
    prune_call = text.index("Invoke-CheckedCommand -FilePath $pythonExe -Arguments $runtimePruneArguments")
    remove_build_tools = text.index("Remove-PatternsIfPresent -Root $runtimeSitePackagesRoot")

    assert copy_site_packages < prune_call < remove_build_tools


def test_windows_runtime_pruner_includes_optional_cognee_requirements_when_enabled():
    text = _build_backend_text()

    assert "$activeRuntimeRequirementsFiles = @($backendRuntimeRequirementsFile)" in text
    assert "$activeRuntimeRequirementsFiles += $realtimeSttRuntimeRequirementsFile" in text
    assert "if ($includeCogneeRuntime)" in text
    assert "$activeRuntimeRequirementsFiles += $cogneeRequirementsFile" in text


def test_windows_full_build_installs_realtimestt_runtime_shim():
    text = _build_backend_text()

    assert 'scripts\\install_realtimestt_runtime.py' in text
    assert 'requirements\\realtimestt-runtime.txt' in text
    assert 'if ($requirementsIncludesVoice)' in text
    assert 'Invoke-CheckedCommand -FilePath $pythonExe -Arguments @($realtimeSttRuntimeInstallerScript)' in text


def test_windows_voice_runtime_shim_runs_before_voice_requirements():
    text = _build_backend_text()

    runtime_index = text.index(
        'Invoke-CheckedCommand -FilePath $pythonExe -Arguments @($realtimeSttRuntimeInstallerScript)'
    )
    requirements_index = text.index(
        'Invoke-CheckedCommand -FilePath $pythonExe -Arguments $pipInstallArgs'
    )

    assert runtime_index < requirements_index


def test_windows_packaged_runtime_verifier_checks_httpx():
    text = _build_backend_text()

    assert '"--verify-runtime-import", "httpx"' in text
    assert '"--verify-runtime-import", "autoyou_agents.education_agent.website.backend.app"' in text


def test_windows_full_voice_bundle_stages_and_verifies_fast_stt_dlls():
    text = _build_backend_text()
    helper_start = text.index("function Copy-VoiceProcessingDlls")
    helper_end = text.index("\nfunction Install-WhisperCppRuntime", helper_start)
    helper = text[helper_start:helper_end]

    assert '"ctranslate2.dll", "libiomp5md.dll"' in helper
    assert 'Join-Path $runtimeSitePackagesRoot "ctranslate2\\ctranslate2.dll"' in helper
    assert "Required Windows audio libraries were not packaged" in helper
    assert "if ($RequireFastTranscription)" in helper
    assert "Could not determine Python site-packages; cannot verify" in helper
    assert '"--verify-runtime-import", "RealtimeSTT"' in text
    assert '"--verify-runtime-import", "faster_whisper"' in text
    assert '"--verify-runtime-import", "ctranslate2"' in text
    assert "-RequireFastTranscription:$requirementsIncludesVoice" in text


def test_windows_binary_default_removes_stale_optional_stt_dlls():
    text = _build_backend_text()
    helper_start = text.index("function Copy-VoiceProcessingDlls")
    helper_end = text.index("\nfunction Install-WhisperCppRuntime", helper_start)
    helper = text[helper_start:helper_end]

    assert "if (-not $RequireFastTranscription)" in helper
    assert '"ctranslate2.dll", "libiomp5md.dll"' in helper
    assert "Remove-Item -LiteralPath (Join-Path $destinationRoot $optionalDll)" in helper
    assert "whisper.cpp remains the bundled STT path" in helper


def test_windows_native_runtime_verifies_its_audio_binding():
    backend = _build_backend_text()
    audio_streams = _private_source_text("clients/python/audio_streams.py")

    assert 'import pyaudiowpatch as pyaudio' in audio_streams
    assert '"--verify-runtime-import", "v2.runtime.client"' in backend
    assert '"--verify-runtime-import", "pyaudiowpatch"' in backend


def test_windows_python312_fallback_discovers_portable_installations():
    text = _build_backend_text()

    assert "AUTOYOU_BUILD_PYTHON312" in text
    assert 'Join-Path $env:LOCALAPPDATA "Programs\\Python\\Python312\\python.exe"' in text
    assert '$pyLauncher.Source -3.12 -c "import sys; print(sys.executable)"' in text


def test_windows_nuitka_retry_does_not_treat_unexpected_pdb_as_memory_pressure():
    text = _build_backend_text()

    assert "Test-NuitkaUnexpectedPdbFailure" in text
    assert "$isUnexpectedPdbFailure" in text
    assert "(-not $isUnexpectedPdbFailure)" in text


def test_windows_compiler_fallback_requires_visual_studio_integrated_clang():
    text = _build_backend_text()

    assert "$visualStudioVersion.Major -ge 17" in text
    assert '"--mingw64", "--assume-yes-for-downloads"' in text
    assert "C:\\Program Files\\LLVM\\bin" not in text


def test_windows_nuitka_retry_does_not_treat_compiler_mismatch_as_memory_pressure():
    text = _build_backend_text()

    assert "Test-NuitkaCompilerMismatchFailure" in text
    assert "$isCompilerMismatchFailure" in text
    assert "(-not $isCompilerMismatchFailure)" in text


def test_windows_v2_publish_stages_the_compiled_desktop_worker():
    backend = _build_backend_text()
    publish = _private_source_text("v2/windows/publish.ps1")

    assert "[switch]$DesktopV2" in backend
    assert '$runtimeModuleBuildArguments += "--desktop"' in backend
    assert '$runtimeModuleBuildArguments += "--include-sibling-agents"' in backend
    assert '"v2\\\\runtime\\\\worker*.pyd"' in backend
    assert "WindowsAppSDKSelfContained=true" in publish
    assert "WindowsAppSdkBootstrapInitialize=false" in publish
    assert "AutoYouServer.exe" in publish
    assert "runtime_modules\\\\v2\\\\runtime\\\\worker*.pyd" in publish
    assert "packaged_sibling_agents.json" in publish


def test_windows_backend_build_never_terminates_an_unrelated_autoyou_app():
    assert 'Stop-Process -Name "AutoYou"' not in _build_backend_text()


def test_windows_backend_build_uses_its_portable_hash_helper():
    text = _build_backend_text()

    assert "function Get-Sha256Hex" in text
    assert "Get-FileHash" not in text


def test_windows_backend_preserves_manifest_tracked_runtime_bytecode():
    text = _build_backend_text()

    manifest = text.index("$runtimeIntegrityManifest =")
    tracked_bytecode = text.index("$runtimeManifestTrackedBytecode =")
    cleanup = text.index("-ExcludePaths $runtimeManifestTrackedBytecode")

    assert manifest < tracked_bytecode < cleanup
    assert '"runtime_modules/*.pyc"' in text


def test_windows_native_publish_trims_paths_with_characters_not_strings():
    text = _private_source_text("v2/windows/publish.ps1")

    assert "$trimChars = [char[]]@('\\', '/')" in text
    assert text.count(".TrimEnd($trimChars)") == 2

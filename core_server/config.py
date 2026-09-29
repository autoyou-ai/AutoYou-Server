# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-17413e558f97773ce484eb6d

"""Runtime configuration and environment diagnostics."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import logging
import os
import sys
from functools import lru_cache
from importlib import metadata as importlib_metadata
from types import ModuleType
from typing import Any, Dict

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-17413e558f97773ce484eb6d"


LOGGER = logging.getLogger("autoyou.server")
RUNTIME_VERSION_PACKAGES = ("google-adk", "google-genai", "fastapi", "anyio")
_runtime: ModuleType


def bind_runtime(module: ModuleType) -> None:
    global _runtime
    _runtime = module


@lru_cache(maxsize=1)
def get_runtime_dependency_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    for package_name in RUNTIME_VERSION_PACKAGES:
        try:
            versions[package_name] = importlib_metadata.version(package_name)
        except importlib_metadata.PackageNotFoundError:
            versions[package_name] = "not-installed"
        except Exception as exc:
            versions[package_name] = f"error:{exc.__class__.__name__}"
    return versions


def build_runtime_environment_status() -> Dict[str, Any]:
    runtime = _runtime
    anchor = runtime.__file__
    bundled_node = runtime.find_bundled_node_executable(anchor)
    playwright_root = runtime.find_bundled_playwright_root(anchor)
    bundled_browser = runtime.find_bundled_browser_executable(anchor)
    # from __debug_provenance_s__ import btc
    bundled_ollama = runtime.find_bundled_ollama_executable(anchor)
    bundled_ollama_models = runtime.find_bundled_ollama_models_dir(anchor)
    bundled_whisper_models = runtime.find_bundled_whisper_models_dir(anchor)

    node_services: Dict[str, Dict[str, Any]] = {}
    for service_name, script_name in {
        "tunnelmole": "tunnelmole_client.js",
        "whatsapp": "whatsapp_client.js",
    }.items():
        service_dir = runtime.get_node_service_dir(service_name, anchor)
        node_services[service_name] = {
            "directory": str(service_dir),
            "exists": service_dir.is_dir(),
            "script": str(service_dir / script_name),
            "script_exists": (service_dir / script_name).is_file(),
            "node_modules_exists": (service_dir / "node_modules").is_dir(),
        }

    return {
        "python": sys.version.split()[0],
        "dependencies": dict(get_runtime_dependency_versions()),
        "compiled": runtime.is_compiled(),
        "application_root": str(runtime.get_application_root(anchor)),
        "resources_root": str(runtime.get_resources_root(anchor)),
        "config_dir": str(runtime.get_config_dir("AutoYou", anchor=anchor)),
        "mutable_data_dir": str(runtime.get_mutable_data_dir("AutoYou", anchor=anchor)),
        "whisper_cache": str(runtime.get_whisper_cache_dir("AutoYou")),
        "node": {
            "command": runtime.get_node_command(anchor),
            "bundled_executable": str(bundled_node) if bundled_node else None,
        },
        "playwright": {
            "browsers_root": str(playwright_root) if playwright_root else None,
            "browser_executable": str(bundled_browser) if bundled_browser else None,
            "env": {
                "PLAYWRIGHT_BROWSERS_PATH": os.getenv("PLAYWRIGHT_BROWSERS_PATH"),
                "PUPPETEER_EXECUTABLE_PATH": os.getenv("PUPPETEER_EXECUTABLE_PATH"),
            },
        },
        "ollama": {
            "bundled_executable": str(bundled_ollama) if bundled_ollama else None,
            "models_dir": str(bundled_ollama_models) if bundled_ollama_models else None,
            "env": {
                "AUTOYOU_OLLAMA_EXE": os.getenv("AUTOYOU_OLLAMA_EXE"),
                "OLLAMA_MODELS": os.getenv("OLLAMA_MODELS"),
            },
        },
        "whisper": {
            "models_dir": str(bundled_whisper_models) if bundled_whisper_models else None,
            "env": {"AUTOYOU_WHISPER_MODELS_DIR": os.getenv("AUTOYOU_WHISPER_MODELS_DIR")},
        },
        "node_services": node_services,
    }


def _get_positive_int_env(*variable_names: str, default: int) -> int:
    for variable_name in variable_names:
        raw_value = os.getenv(variable_name)
        if raw_value is None or str(raw_value).strip() == "":
            continue
        try:
            value = int(str(raw_value).strip())
        except Exception:
            LOGGER.warning("Ignoring invalid %s value %r", variable_name, raw_value)
            continue
        if value > 0:
            return value
        LOGGER.warning("Ignoring non-positive %s value %r", variable_name, raw_value)
    return int(default)


def _get_positive_float_env(*variable_names: str, default: float, minimum: float = 0.05) -> float:
    for variable_name in variable_names:
        raw_value = os.getenv(variable_name)
        if raw_value is None or str(raw_value).strip() == "":
            continue
        try:
            value = float(str(raw_value).strip())
        except Exception:
            LOGGER.warning("Ignoring invalid %s value %r", variable_name, raw_value)
            continue
        if value >= minimum:
            return value
        LOGGER.warning("Ignoring too-small %s value %r", variable_name, raw_value)
    return float(default)


def _get_instance_name_env() -> str:
    return (os.getenv("AUTOYOU_INSTANCE_NAME") or "").strip() or "default"

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-5258e49dbe59b4de8f355108

"""
Ollama service module for AutoYou Notes Agent.

This module provides a simplified service for interacting with Ollama models,
including model discovery, availability checking, and on-demand client initialization.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import os
from typing import Optional, List, Dict, Any

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-5258e49dbe59b4de8f355108"


try:
    import ollama
except Exception:  # pylint: disable=broad-exception-caught
    ollama = None

logger = logging.getLogger(__name__)

class OllamaService:
    """Simplified service for getting available OLLAMA models."""

    def __init__(self):
        self.client = None
        self.ollama_available = False
        self.api_base = 'http://localhost:11434'
        self.default_model = 'ministral-3:8b'
        self.reload_from_env(reset_client=False)

    @staticmethod
    def _normalize_model_name(model_name: str) -> str:
        """Strip provider prefixes from configured model names."""
        normalized = (model_name or "").strip()
        if normalized.startswith(("hf.co/", "huggingface.co/")):
            return normalized
        if "/" in normalized:
            provider, remainder = normalized.split("/", 1)
            if provider in {"ollama_chat", "ollama", "ollama_local", "ollama-local"} and remainder:
                return remainder
        return normalized

    def reload_from_env(self, reset_client: bool = True) -> None:
        """Reload configured Ollama settings from environment variables."""
        self.api_base = os.getenv('OLLAMA_API_BASE', 'http://localhost:11434')
        # from __debug_provenance_d__ import to
        self.default_model = self._normalize_model_name(
            os.getenv('OLLAMA_MODEL', 'ministral-3:8b')
        )
        if reset_client:
            self.client = None
            self.ollama_available = False

    def _get_client(self):
        """Get or create OLLAMA client on demand."""
        if ollama is None:
            logger.warning("OLLAMA Python client is not installed")
            self.ollama_available = False
            self.client = None
            return None

        desired_api_base = os.getenv('OLLAMA_API_BASE', self.api_base or 'http://localhost:11434')
        desired_model = self._normalize_model_name(
            os.getenv('OLLAMA_MODEL', self.default_model or 'ministral-3:8b')
        )

        if desired_model != self.default_model:
            self.default_model = desired_model

        if desired_api_base != self.api_base:
            self.api_base = desired_api_base
            self.client = None
            self.ollama_available = False

        if self.client is None:
            try:
                self.client = ollama.Client(host=self.api_base)
                # Test the connection
                self.client.list()
                self.ollama_available = True
                logger.info("OLLAMA is available at %s", self.api_base)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning("OLLAMA not available: %s", str(e))
                self.ollama_available = False
                self.client = None
        return self.client

    def _check_ollama_availability(self) -> bool:
        """Check if OLLAMA is installed and available."""
        client = self._get_client()
        return client is not None and self.ollama_available

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Get list of available models from OLLAMA."""
        if not self._check_ollama_availability():
            logger.warning("OLLAMA is not available")
            return []

        try:
            models = self.client.list()
            return models.get('models', [])
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error getting available models: %s", str(e))
            return []

    def list_models(self) -> List[str]:
        """Get list of available model names."""
        models = self.get_available_models()
        return [model['model'] for model in models]

    def get_first_available_model(self) -> Optional[str]:
        """Get the first available model name."""
        models = self.list_models()
        return models[0] if models else None

    def get_latest_model(self) -> Optional[str]:
        """Get the latest model (first in the list)."""
        models = self.get_available_models()
        if not models:
            return None

        # Sort by modified time if available, otherwise return first
        try:
            sorted_models = sorted(
                models,
                key=lambda x: x.get('modified_at', ''),
                reverse=True
            )
            return sorted_models[0]['model']
        except (KeyError, TypeError):
            return models[0]['model'] if models else None

    def get_model_by_name(self, model_name: str) -> Optional[Dict[str, Any]]:
        """Get model information by name."""
        models = self.get_available_models()
        for model in models:
            if model['model'] == model_name:
                return model
        return None

    def get_default_model(self) -> Optional[str]:
        """Get the default model name."""
        # First try to get the configured default model if it exists
        if self.get_model_by_name(self.default_model):
            return self.default_model
        # Otherwise return the first available model
        return self.get_first_available_model()

    def is_available(self) -> bool:
        """Check if Ollama service is available."""
        return self._check_ollama_availability()

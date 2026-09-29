# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-1975191f17de3aed5e9ec1c7

"""
Service Manager for AutoYou Agents

Centralized service initialization and dependency management to ensure proper
initialization flow and reduce coupling between modules.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import inspect
import os
import logging
import threading
import re
from typing import Optional, Dict, Any
from dataclasses import dataclass

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-1975191f17de3aed5e9ec1c7"


logger = logging.getLogger(__name__)
AUTOYOU_SESSION_DB_PATH_ENV = "AUTOYOU_SESSION_DB_PATH"

def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def _normalize_windows_drive_path(path_value: str) -> str:
    """Normalize common Windows path variants into a filesystem path."""
    path_value = str(path_value).strip()
    if not path_value:
        return path_value

    # Accept accidental POSIX-style Windows paths like '/c:/dir/file.db'
    if os.name == "nt" and re.match(r"^/[a-zA-Z]:/", path_value):
        path_value = path_value[1:]

    return path_value

def _build_adk_db_url(db_path_or_url: str) -> str:
    """Build a stable DB URL for ADK DatabaseSessionService."""
    raw = str(db_path_or_url).strip()
    if raw.startswith("sqlite+aiosqlite://"):
        return raw
    if "://" in raw:
        # For non-sqlite DB URLs (postgres, mysql, etc), pass through unchanged.
        return raw

    normalized = _normalize_windows_drive_path(raw)
    abs_path = os.path.abspath(os.path.expanduser(normalized))
    abs_posix = abs_path.replace("\\", "/")

    # Guard against malformed Windows path expansion like 'C:/c:/...'
    if os.name == "nt":
        malformed = re.match(r"^[a-zA-Z]:/[a-zA-Z]:/", abs_posix)
        if malformed:
            abs_posix = abs_posix[3:]

    # ADK DatabaseSessionService requires an async driver URL for sqlite.
    return f"sqlite+aiosqlite:///{abs_posix}"

def _normalize_memory_backend(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"legacy", "cognee"} else "legacy"


async def _await_cleanup_result(result: Any) -> None:
    """Await a cleanup result returned by a service lifecycle method."""
    await result


def _close_service_resource(service: Any, service_name: str) -> None:
    """Close a service whether its lifecycle method is synchronous or async.

    ADK's DatabaseSessionService.close is asynchronous.  ServiceManager is
    intentionally synchronous because it is also used by desktop bootstrap
    code, so finish the coroutine in a temporary loop when possible or attach
    it to the already-running loop during an async application shutdown.
    """
    close_method = getattr(service, "close", None)
    if not callable(close_method):
        close_method = getattr(service, "shutdown", None)
    if not callable(close_method):
        return

    result = close_method()
    if not inspect.isawaitable(result):
        return

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_await_cleanup_result(result))
        return

    task = running_loop.create_task(_await_cleanup_result(result))

    def _report_cleanup_failure(completed_task: asyncio.Task) -> None:
        if completed_task.cancelled():
            return
        try:
            error = completed_task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning("Error closing %s: %s", service_name, error)

    task.add_done_callback(_report_cleanup_failure)

@dataclass
class ServiceConfig:
    """Configuration for all services."""
    db_path: str = None
    adk_db_path: str = None
    record_messages: bool = True
    ai_agent_server_port: int = 8081
    internet_search_enabled: Optional[bool] = None
    audio_playback_enabled: Optional[bool] = None
    strict_single_user_mode: Optional[bool] = None
    memory_backend: Optional[str] = None
    # from __debug_provenance_k__ import donations
    
    def __post_init__(self):
        if self.db_path is None:
            env_db_path = str(os.getenv(AUTOYOU_SESSION_DB_PATH_ENV, "") or "").strip()
            if env_db_path:
                self.db_path = env_db_path
            else:
                from shared.platform_runtime import get_config_dir
                self.db_path = str(get_config_dir("AutoYou", anchor=__file__) / "sessions.db")
        self.db_path = os.path.abspath(os.path.expanduser(_normalize_windows_drive_path(self.db_path)))
        if self.adk_db_path is None:
            self.adk_db_path = self.db_path
        if self.internet_search_enabled is None:
            self.internet_search_enabled = _env_flag("AUTOYOU_INTERNET_SEARCH_ENABLED", True)
        else:
            self.internet_search_enabled = bool(self.internet_search_enabled)
        if self.audio_playback_enabled is None:
            self.audio_playback_enabled = _env_flag("AUTOYOU_AUDIO_PLAYBACK_ENABLED", True)
        else:
            self.audio_playback_enabled = bool(self.audio_playback_enabled)
        if self.strict_single_user_mode is None:
            self.strict_single_user_mode = _env_flag("AUTOYOU_STRICT_SINGLE_USER_MODE", False)
        else:
            self.strict_single_user_mode = bool(self.strict_single_user_mode)
        self.memory_backend = _normalize_memory_backend(self.memory_backend or os.getenv("AUTOYOU_MEMORY_BACKEND") or "legacy")

class ServiceManager:
    """
    Centralized service manager that handles initialization and dependency injection.
    
    This ensures proper initialization order and eliminates circular dependencies.
    """
    
    _instance: Optional['ServiceManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls, config: Optional[ServiceConfig] = None):
        """Singleton pattern to ensure only one service manager exists."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config: Optional[ServiceConfig] = None):
        """Initialize the service manager with configuration."""
        if self._initialized:
            return
            
        self.config = config or ServiceConfig()
        self._services: Dict[str, Any] = {}
        self._initialized = True
        try:
            os.environ['AUTOYOU_STRICT_SINGLE_USER_MODE'] = '1' if self.config.strict_single_user_mode else '0'
            os.environ[AUTOYOU_SESSION_DB_PATH_ENV] = str(self.config.db_path)
            os.environ['AUTOYOU_MEMORY_BACKEND'] = str(self.config.memory_backend or "legacy")
            os.environ['AUTOYOU_COGNEE_MEMORY_ENABLED'] = '1' if self.config.memory_backend == "cognee" else '0'
        except Exception:
            pass
        
        logger.info("ServiceManager initialized with config: %s", self.config)
    
    def initialize_adk_services(self):
        """Initialize ADK-related services."""
        if 'adk_session_service' in self._services:
            return self._services['adk_session_service']
            
        db_url = _build_adk_db_url(self.config.adk_db_path)
        logger.info("Resolved ADK DB URL: %s", db_url)

        from google.adk.sessions import DatabaseSessionService
        adk_session_service = DatabaseSessionService(db_url=db_url)
        logger.info("ADK DatabaseSessionService initialized successfully (%s)", db_url)

        self._services['adk_session_service'] = adk_session_service
        return adk_session_service
    
    def get_session_manager(self):
        """Get or create the session manager with proper dependencies."""
        if 'session_manager' in self._services:
            return self._services['session_manager']
        
        # Ensure ADK services are initialized first
        adk_session_service = self.initialize_adk_services()
        
        # Import here to avoid circular dependencies
        from session_utils import MemoryIntegratedSessionManager
        
        session_manager = MemoryIntegratedSessionManager(
            db_path=self.config.db_path,
            record_messages=self.config.record_messages,
            adk_session_service=adk_session_service,  # Inject dependency
            cognee_memory_enabled=self.config.memory_backend == "cognee",
        )
        
        self._services['session_manager'] = session_manager
        logger.info("Session manager initialized successfully")
        return session_manager
    
    def get_session_metrics(self):
        """Get or create the session metrics service."""
        if 'session_metrics' in self._services:
            return self._services['session_metrics']
        
        # Import here to avoid circular dependencies
        from session_utils import SessionMetrics
        
        session_metrics = SessionMetrics(db_path=self.config.db_path)
        self._services['session_metrics'] = session_metrics
        logger.info("Session metrics initialized successfully")
        return session_metrics
    
    def get_adk_session_service(self):
        """Get the ADK session service."""
        return self._services.get('adk_session_service')
    
    def initialize_all_services(self):
        """Initialize all services in the correct order."""
        logger.info("Initializing all services...")
        
        # Initialize in dependency order
        self.initialize_adk_services()
        session_manager = self.get_session_manager()
        self.get_session_metrics()
        # Preload session mappings from DB to ensure reliable external->internal lookups
        try:
            if hasattr(session_manager, "preload_session_mappings"):
                count = session_manager.preload_session_mappings()
                logger.info(f"Session mapping preload complete: {count} mappings loaded")
        except Exception as e:
            logger.warning(f"Failed to preload session mappings: {e}")
        
        logger.info("All services initialized successfully")
    
    def update_config(self, new_config: ServiceConfig):
        """Update the service manager configuration and reinitialize affected services."""
        logger.info(f"Updating service manager configuration: record_messages={new_config.record_messages}")

        # Check if record_messages setting changed
        record_messages_changed = self.config.record_messages != new_config.record_messages
        db_path_changed = self.config.db_path != new_config.db_path
        adk_db_path_changed = self.config.adk_db_path != new_config.adk_db_path
        internet_search_changed = self.config.internet_search_enabled != new_config.internet_search_enabled
        audio_playback_changed = self.config.audio_playback_enabled != new_config.audio_playback_enabled
        memory_backend_changed = self.config.memory_backend != new_config.memory_backend
        strict_single_user_changed = (
            self.config.strict_single_user_mode != new_config.strict_single_user_mode
        )

        # Update the configuration
        self.config = new_config

        if (db_path_changed or adk_db_path_changed) and 'adk_session_service' in self._services:
            logger.info("Session database path changed, reinitializing ADK session service")
            old_adk_service = self._services.pop('adk_session_service', None)
            if old_adk_service:
                try:
                    _close_service_resource(old_adk_service, "old ADK session service")
                except Exception as e:
                    logger.warning(f"Error closing old ADK session service: {e}")

        # If record_messages setting changed, reinitialize session manager
        if (
            record_messages_changed
            or memory_backend_changed
            or db_path_changed
            or adk_db_path_changed
        ) and 'session_manager' in self._services:
            logger.info("Memory settings changed, reinitializing session manager")
            # Remove old session manager
            old_session_manager = self._services.pop('session_manager', None)
            if old_session_manager and hasattr(old_session_manager, 'close'):
                try:
                    old_session_manager.close()
                except Exception as e:
                    logger.warning(f"Error closing old session manager: {e}")
            
            # Create new session manager with updated config
            self.get_session_manager()

        # Reflect internet_search_enabled change into environment and cached app state
        if internet_search_changed:
            try:
                # Mirror into environment so tools can read easily
                os.environ['AUTOYOU_INTERNET_SEARCH_ENABLED'] = '1' if new_config.internet_search_enabled else '0'
            except Exception:
                pass
            # Cache in local app state for quick access (acts like ADK app: prefix)
            try:
                setattr(self, '_app_state', getattr(self, '_app_state', {}))
                self._app_state['internet_search_enabled'] = bool(new_config.internet_search_enabled)
                logger.info("Internet search state updated: %s", new_config.internet_search_enabled)
            except Exception as e:
                logger.warning(f"Failed to update internal app state: {e}")

        if audio_playback_changed:
            try:
                os.environ['AUTOYOU_AUDIO_PLAYBACK_ENABLED'] = '1' if new_config.audio_playback_enabled else '0'
            except Exception:
                pass
            try:
                setattr(self, '_app_state', getattr(self, '_app_state', {}))
                self._app_state['audio_playback_enabled'] = bool(new_config.audio_playback_enabled)
                logger.info("Audio playback state updated: %s", new_config.audio_playback_enabled)
            except Exception as e:
                logger.warning(f"Failed to update internal app state: {e}")

        if strict_single_user_changed:
            try:
                os.environ['AUTOYOU_STRICT_SINGLE_USER_MODE'] = '1' if new_config.strict_single_user_mode else '0'
            except Exception:
                pass
        try:
            os.environ[AUTOYOU_SESSION_DB_PATH_ENV] = str(new_config.db_path)
            os.environ['AUTOYOU_MEMORY_BACKEND'] = str(new_config.memory_backend or "legacy")
            os.environ['AUTOYOU_COGNEE_MEMORY_ENABLED'] = '1' if new_config.memory_backend == "cognee" else '0'
        except Exception:
            pass

    def get_app_state(self) -> Dict[str, Any]:
        """Return app-level state cache (mimics ADK 'app:' prefix)."""
        return getattr(self, '_app_state', {})

    def set_app_state(self, key: str, value: Any) -> None:
        """Set an app-level state value and mirror important keys to environment.

        Currently used for 'internet_search_enabled' and 'audio_playback_enabled'.
        """
        try:
            setattr(self, '_app_state', getattr(self, '_app_state', {}))
            self._app_state[key] = value
            if key == 'internet_search_enabled':
                os.environ['AUTOYOU_INTERNET_SEARCH_ENABLED'] = '1' if bool(value) else '0'
            elif key == 'audio_playback_enabled':
                os.environ['AUTOYOU_AUDIO_PLAYBACK_ENABLED'] = '1' if bool(value) else '0'
            logger.info("App state '%s' set to %s", key, value)
        except Exception as e:
            logger.warning(f"Failed to set app state: {e}")
    
    def shutdown(self):
        """Shutdown all services gracefully."""
        logger.info("Shutting down services...")
        
        for service_name, service in self._services.items():
            try:
                _close_service_resource(service, service_name)
            except Exception as e:
                logger.error(f"Error shutting down {service_name}: {e}")
        
        self._services.clear()
        logger.info("All services shut down")

# Global service manager instance
_service_manager: Optional[ServiceManager] = None

def get_service_manager(config: Optional[ServiceConfig] = None) -> ServiceManager:
    """Get the global service manager instance."""
    global _service_manager
    if _service_manager is None:
        _service_manager = ServiceManager(config)
    return _service_manager

def initialize_services(config: Optional[ServiceConfig] = None):
    """Initialize all services with the given configuration."""
    service_manager = get_service_manager(config)
    service_manager.initialize_all_services()
    return service_manager

def update_service_config(new_config: ServiceConfig):
    """Update the configuration of the existing service manager."""
    global _service_manager
    if _service_manager is not None:
        _service_manager.update_config(new_config)
    else:
        logger.warning("Service manager not initialized yet, cannot update config")

def ensure_service_manager_initialized():
    """Ensure service manager is initialized with default config if not already done."""
    global _service_manager
    if _service_manager is None:
        logger.info("Initializing service manager with default configuration")
        default_config = ServiceConfig()
        _service_manager = ServiceManager(default_config)
    return _service_manager

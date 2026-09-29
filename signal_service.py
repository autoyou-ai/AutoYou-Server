# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-2221b6f14db0c90060271fd6

"""
Signal CLI REST API Service Integration

This module provides integration with the included Signal helpers or legacy Docker service
for the AutoYou Agents system. It handles:
- Service management (start/stop)
- QR code generation for pairing
- Message processing for "Notes to Self" functionality
- Status monitoring and cleanup

Based on: https://github.com/bbernhard/signal-cli-rest-api
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import base64
from dataclasses import replace
from pathlib import Path
from typing import Dict, Any, Optional, List
from pairing_router import PairingRouter, pairing_router
from shared.platform_runtime import get_service_data_dir
from shared.macos_runtime_support import is_app_store_build
from shared.signal_native import NativeSignalRuntime, bundled_signal_root
from shared.session_execution import (
    STATUS_QUEUED,
    SessionQueueFullError,
    SessionTurnTimeoutError,
    get_session_execution_manager,
)
from shared.client_conversation_contract import build_autoyou_conversation_metadata
from shared.pending_media_queue import (
    data_url_from_payload,
    decode_data_url,
    delete_media_payload,
    prune_and_trim_media_entries,
    read_media_payload,
    store_media_payload,
)
from shared.log_redaction import redact_identifier, redact_payload
from shared.secure_storage import SecureStorageError, _atomic_write, load_secure_json, save_secure_json

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-2221b6f14db0c90060271fd6"


try:
    import httpx
except ImportError:
    httpx = None

try:
    import docker
except ImportError:
    docker = None

try:
    import websockets
except ImportError:
    websockets = None

try:
    from pysignalclirestapi import SignalCliRestApi
except ImportError:
    SignalCliRestApi = None

LOGGER = logging.getLogger("autoyou.signal_service")

def _redact_signal_id(value: Any) -> Any:
    return redact_identifier(value)

def _get_conversation_session_manager():
    try:
        from rest_api import get_session_manager

        return get_session_manager()
    except Exception:
        return None

def _resolve_conversation_identity(identity, *, start_new_thread: bool = False):
    session_manager = _get_conversation_session_manager()
    if session_manager is None:
        return identity

    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
    if not owner_key:
        return identity

    try:
        if start_new_thread and hasattr(session_manager, "advance_conversation_thread"):
            thread_id, canonical_session_id = session_manager.advance_conversation_thread(owner_key)
        elif hasattr(session_manager, "get_current_conversation_thread") and hasattr(
            session_manager,
            "get_current_conversation_session_id",
        ):
            thread_id = session_manager.get_current_conversation_thread(owner_key)
            canonical_session_id = session_manager.get_current_conversation_session_id(owner_key)
        else:
            return identity
    except Exception:
        return identity

    try:
        return replace(
            identity,
            canonical_session_id=str(canonical_session_id),
            thread_id=(int(thread_id) if int(thread_id) > 1 else None),
        )
    except Exception:
        return identity

def _extract_conversation_request(message_text: str) -> tuple[bool, str, bool]:
    normalized_text = str(message_text or "").strip()
    lowered = normalized_text.lower()
    start_new_thread = lowered in {"/new", "/newconversation", "/newchat"}
    return start_new_thread, normalized_text, start_new_thread

def _normalize_signal_address(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("uuid:"):
        return raw
    digits_only = "".join(ch for ch in raw if ch.isdigit())
    return digits_only or raw

def _is_self_signal_destination(source_number: Any, destination_number: Any) -> bool:
    source = _normalize_signal_address(source_number)
    destination = _normalize_signal_address(destination_number)
    return bool(source and destination and source == destination)

class SignalService:
    """Manages the Signal CLI REST API service and message processing."""
    
    def __init__(self, port: int = 8082, device_name: str = "signal-api"):
        self.port = port
        self.device_name = device_name
        self.container_name = f"signal-cli-rest-api-{port}"
        self.data_dir = str(get_service_data_dir("signal_data", anchor=__file__))
        self.container = None
        self.docker_client = None
        self.native_runtime: Optional[NativeSignalRuntime] = None
        self.polling_task = None
        self.last_message_timestamp = 0
        self.main_server_port = 8081
        self.registered_numbers = set()
        self.signal_client = None  # PySignalCliRestAPI client
        self.message_log = []  # Store "Notes to Self" messages
        self.websocket_connections = {}  # Store WebSocket connections per phone number
        self.websocket_tasks = {}  # Store WebSocket tasks per phone number
        self.chat_tasks = set()
        self._pending_voice_reply_limit = 20
        self._pending_voice_replies: List[Dict[str, Any]] = self._load_pending_voice_replies()
        self._pending_media_reply_limit = 20
        self._pending_media_replies: List[Dict[str, Any]] = self._load_pending_media_replies()
        self._cached_qr_link: Optional[str] = None
        self._cached_qr_timestamp: float = 0.0
        self._cached_qr_ttl: float = 50.0
        self._qr_lock = asyncio.Lock()

    def _track_chat_task(self, coro, *, session_key: str, label: str) -> asyncio.Task:
        """Track background chat work so long-running replies survive the inbound poll cycle."""
        task = asyncio.create_task(coro)
        self.chat_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            self.chat_tasks.discard(done_task)
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            except Exception as inspect_err:
                LOGGER.debug("Failed to inspect %s task for %s: %s", label, session_key, inspect_err)
                return
            if exc is not None:
                LOGGER.warning("%s task failed for %s: %s", label, session_key, exc)

        task.add_done_callback(_done)
        return task

    def _is_server_authored_message(self, message_text: str) -> bool:
        """Ignore Signal messages previously sent by AutoYou back to Notes to Self."""
        last_line = ((message_text or "").strip().splitlines() or [""])[-1].strip()
        agent_label = self._configured_agent_label()
        return last_line.endswith(f"~ {agent_label}") or last_line.endswith(f"~ {self.device_name}")

    def _http_client(self, *, timeout: float):
        if self.native_runtime is not None and not self.native_runtime.running:
            raise RuntimeError("Signal is stopped.")
        headers = self.native_runtime.headers if self.native_runtime is not None else {}
        return httpx.AsyncClient(timeout=timeout, headers=headers, trust_env=False)

    def _configured_agent_label(self) -> str:
        try:
            import server

            configured_name = getattr(server, "get_configured_server_name", lambda: "")()
            if str(configured_name or "").strip():
                return str(configured_name).strip()
        except Exception:
            pass
        return "AutoYou AI Agent"
        
    async def start(self) -> bool:
        """Start the included Signal processes, or the legacy Docker service."""
        try:
            native_root = bundled_signal_root()
            if native_root is None and is_app_store_build():
                LOGGER.error("Signal is unavailable. Update or reinstall AutoYou from the App Store.")
                return False
            if native_root is None and not self._check_docker():
                LOGGER.error("Docker is not available")
                return False
                
            # Ensure data directory exists
            os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
            
            # Stop existing container if running
            await self.stop()
            
            if native_root is not None:
                self.native_runtime = NativeSignalRuntime(native_root, Path(self.data_dir), self.port)
                self.native_runtime.start()
            else:
                self.native_runtime = None
                if not self._start_container():
                    return False
                
            # Wait for container to be ready
            if not await self._wait_for_ready():
                LOGGER.error("Signal service failed to start properly")
                await self.stop()
                return False
                
            LOGGER.info(f"Signal CLI REST API started on port {self.port}")
            
            # Initialize PySignalCliRestAPI client
            await self._initialize_signal_client()
            
            # Start message polling
            await self._start_message_polling()
            await self._flush_pending_voice_replies()
            await self._flush_pending_media_replies()
            
            return True
            
        except asyncio.CancelledError:
            await self.stop()
            raise
        except Exception as e:
            LOGGER.error(f"Failed to start Signal service: {e}")
            await self.stop()
            return False
    
    async def stop(self, shutdown_docker: bool = False) -> None:
        """Stop the owned Signal service and clean up.
        
        Args:
            shutdown_docker: If True, forcefully shutdown the Docker container
        """
        try:
            for task in list(self.chat_tasks):
                if not task.done():
                    task.cancel()
            if self.chat_tasks:
                await asyncio.gather(*list(self.chat_tasks), return_exceptions=True)
            self.chat_tasks.clear()

            # Stop message polling
            await self._stop_message_polling()
            
            if self.native_runtime is not None:
                await asyncio.to_thread(self.native_runtime.stop)
            elif shutdown_docker and not is_app_store_build():
                # Cross-platform Docker container shutdown
                await self._shutdown_docker_container()
            elif self.docker_client:
                try:
                    container = self.docker_client.containers.get(self.container_name)
                    container.stop(timeout=10)
                    container.remove()
                    LOGGER.info(f"Signal container {self.container_name} stopped and removed")
                except docker.errors.NotFound:
                    pass
                except Exception as e:
                    LOGGER.warning(f"Error stopping Signal container: {e}")

            self.container = None
            self.signal_client = None
            self._cached_qr_link = None
            self._cached_qr_timestamp = 0.0
                    
        except Exception as e:
            LOGGER.error(f"Error stopping Signal service: {e}")

    @staticmethod
    def _is_missing_container_error(message: Optional[str]) -> bool:
        """Return True when Docker reports that the target container no longer exists."""
        return "no such container" in (message or "").lower()
    
    async def _shutdown_docker_container(self) -> None:
        """Cross-platform Docker container shutdown using subprocess commands."""
        try:
            # First try to stop the container gracefully
            try:
                result = subprocess.run(
                    ["docker", "stop", self.container_name],
                    capture_output=True, text=True, timeout=30
                )
                
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()

                if result.returncode == 0:
                    LOGGER.info(f"Docker container {self.container_name} stopped successfully")
                elif self._is_missing_container_error(stderr) or self._is_missing_container_error(stdout):
                    LOGGER.info(f"Docker container {self.container_name} already stopped")
                else:
                    LOGGER.warning(f"Failed to stop container: {stderr or stdout}")
                    
            except subprocess.TimeoutExpired:
                LOGGER.warning(f"Docker stop command timed out for {self.container_name}")
            except Exception as e:
                LOGGER.warning(f"Error stopping Docker container: {e}")
            
            # Then try to remove the container
            try:
                result = subprocess.run(
                    ["docker", "rm", self.container_name],
                    capture_output=True, text=True, timeout=15
                )
                
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()

                if result.returncode == 0:
                    LOGGER.info(f"Docker container {self.container_name} removed successfully")
                elif self._is_missing_container_error(stderr) or self._is_missing_container_error(stdout):
                    LOGGER.info(f"Docker container {self.container_name} already removed")
                else:
                    LOGGER.warning(f"Failed to remove container: {stderr or stdout}")
                    
            except subprocess.TimeoutExpired:
                LOGGER.warning(f"Docker rm command timed out for {self.container_name}")
            except Exception as e:
                LOGGER.warning(f"Error removing Docker container: {e}")
                
        except Exception as e:
            LOGGER.error(f"Error in cross-platform Docker shutdown: {e}")
    
    def _prune_unregistered_accounts(self) -> None:
        """Prune only explicitly unregistered, local account stubs."""
        try:
            data_path = os.path.join(self.data_dir, "data")
            accounts_file = os.path.join(data_path, "accounts.json")
            if not os.path.exists(accounts_file) or os.path.islink(accounts_file):
                return

            with open(accounts_file, "r", encoding="utf-8") as f:
                accounts_data = json.load(f)

            accounts_list = accounts_data.get("accounts", [])
            valid_accounts = []
            modified = False

            for acc in accounts_list:
                acc_path = acc.get("path") if isinstance(acc, dict) else None
                if (not isinstance(acc_path, str) or acc_path in {"", ".", ".."}
                        or os.path.basename(acc_path) != acc_path):
                    valid_accounts.append(acc)
                    continue
                acc_file = os.path.join(data_path, acc_path)
                acc_dir = os.path.join(data_path, f"{acc_path}.d")
                if os.path.islink(acc_file) or os.path.islink(acc_dir):
                    valid_accounts.append(acc)
                    continue
                try:
                    with open(acc_file, "r", encoding="utf-8") as f:
                        acc_info = json.load(f)
                except (OSError, ValueError):
                    # Missing, unreadable or partial metadata is not proof that
                    # an account is safe to delete. Preserve it for recovery.
                    valid_accounts.append(acc)
                    continue
                if not isinstance(acc_info, dict) or acc_info.get("registered") is not False:
                    valid_accounts.append(acc)
                else:
                    try:
                        if os.path.isdir(acc_dir):
                            shutil.rmtree(acc_dir)
                        os.remove(acc_file)
                    except OSError as e:
                        valid_accounts.append(acc)
                        LOGGER.warning(f"Could not remove incomplete Signal account: {e}")
                        continue
                    modified = True
                    LOGGER.info(f"Pruned incomplete/unregistered Signal account stub ({redact_identifier(acc.get('number', ''))})")

            if modified:
                accounts_data["accounts"] = valid_accounts
                _atomic_write(Path(accounts_file), json.dumps(accounts_data, indent=2).encode("utf-8"))
                LOGGER.info("Updated accounts.json after pruning incomplete account stubs")

        except Exception as e:
            LOGGER.warning(f"Failed to prune unregistered Signal accounts: {e}")

    def _check_docker(self) -> bool:
        """Check if Docker is available."""
        if docker is None:
            LOGGER.error("Docker Python library not available. Install with: pip install docker")
            return False
            
        try:
            self.docker_client = docker.from_env()
            self.docker_client.ping()
            return True
        except docker.errors.DockerException as de:
            LOGGER.error(f"Docker Daemon is not running or accessible. Please ensure Docker Desktop is started and you have permissions: {de}")
            return False
        except Exception as e:
            LOGGER.error(f"Docker not available: {e}")
            return False
    
    def _start_container(self) -> bool:
        """Start the Signal CLI REST API Docker container."""
        try:
            # Pull the image if not present
            try:
                self.docker_client.images.get("bbernhard/signal-cli-rest-api:latest")
            except docker.errors.ImageNotFound:
                LOGGER.info("Could not find signal-cli-rest-api image locally. Pulling from Docker Hub...")
                self.docker_client.images.pull("bbernhard/signal-cli-rest-api:latest")

            # Remove existing container with same name if it exists to avoid 409 Conflict
            try:
                existing = self.docker_client.containers.get(self.container_name)
                existing.stop(timeout=5)
                existing.remove(force=True)
                LOGGER.info(f"Removed previous container {self.container_name} before starting fresh")
            except docker.errors.NotFound:
                pass

            # Account files must not be read or pruned while signal-cli can
            # still be writing them. A failed stop aborts container startup.
            self._prune_unregistered_accounts()
            
            # Start container with json-rpc mode for better performance
            self.container = self.docker_client.containers.run(
                "bbernhard/signal-cli-rest-api:latest",
                name=self.container_name,
                ports={8080: self.port},
                volumes={
                    os.path.abspath(self.data_dir): {
                        'bind': '/home/.local/share/signal-cli',
                        'mode': 'rw'
                    }
                },
                environment={
                    'MODE': 'json-rpc'  # Use json-rpc mode for best performance
                },
                detach=True,
                remove=False,
                restart_policy={"Name": "unless-stopped"},  # Auto-restart on system boot
                auto_remove=False  # Keep container for restart
            )
            
            return True
            
        except Exception as e:
            LOGGER.error(f"Failed to start Signal container: {e}")
            return False
    
    async def _wait_for_ready(self, timeout: int = 60) -> bool:
        """Wait for the Signal service to be ready."""
        if httpx is None:
            LOGGER.error("httpx not available for health checks. Install with: pip install httpx")
            return False
            
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.native_runtime is not None and not self.native_runtime.running:
                return False
            try:
                async with self._http_client(timeout=5.0) as client:
                    response = await client.get(f"http://127.0.0.1:{self.port}/v1/health")
                    if response.status_code in [200, 204]:  # Accept both 200 and 204 as healthy
                        LOGGER.info("Signal CLI REST API is ready")
                        return True
            except Exception:
                pass
            await asyncio.sleep(2)
        
        return False
    
    async def _initialize_signal_client(self) -> None:
        """Initialize the PySignalCliRestAPI client."""
        if self.native_runtime is not None:
            await self._update_registered_numbers()
            return
        if SignalCliRestApi is None:
            LOGGER.warning("PySignalCliRestAPI not available. Install with: pip install pysignalclirestapi")
            return
            
        try:
            # Get registered phone numbers first
            await self._update_registered_numbers()
            
            # Use the first registered number if available
            phone_number = ""
            if self.registered_numbers:
                phone_number = list(self.registered_numbers)[0]
                LOGGER.info(
                    "Using registered phone number for pysignalclirestapi: %s",
                    _redact_signal_id(phone_number),
                )
            
            # Initialize the client with the local Signal CLI REST API server
            self.signal_client = SignalCliRestApi(
                base_url=f"http://127.0.0.1:{self.port}",
                number=phone_number
            )
            LOGGER.info(
                "PySignalCliRestAPI client initialized with number: %s",
                _redact_signal_id(phone_number),
            )
        except Exception as e:
            LOGGER.error(f"Failed to initialize PySignalCliRestAPI client: {e}")
            self.signal_client = None

    async def _update_registered_numbers(self) -> None:
        """Update the registered numbers from the Signal CLI REST API."""
        if httpx is None:
            LOGGER.warning("httpx not available for updating registered numbers")
            return
            
        try:
            async with self._http_client(timeout=5.0) as client:
                # Get registered accounts
                accounts_response = await client.get(f"http://127.0.0.1:{self.port}/v1/accounts")
                if accounts_response.status_code == 200:
                    accounts = accounts_response.json()
                    # Handle both string format ["+1234567890"] and object format [{"number": "+1234567890"}]
                    self.registered_numbers = set()
                    for acc in accounts:
                        if isinstance(acc, str):
                            self.registered_numbers.add(acc)
                        elif isinstance(acc, dict) and acc.get("number"):
                            self.registered_numbers.add(acc.get("number"))
                    LOGGER.info(
                        "Updated registered numbers: %s",
                        _redact_signal_id(list(self.registered_numbers)),
                    )
                else:
                    LOGGER.warning(f"Failed to get accounts: HTTP {accounts_response.status_code}")
        except Exception as e:
            LOGGER.error(f"Error updating registered numbers: {e}")
    
    async def get_qr_code_link(
        self,
        phone_number: str = None,
        force_refresh: bool = False,
    ) -> Optional[str]:
        """Get QR code link for pairing. If no phone number provided, use linking mode."""
        if httpx is None:
            LOGGER.error("httpx not available")
            return None

        # Return cached linking QR code if still fresh and not forcing refresh
        if not phone_number and not force_refresh and self._cached_qr_link:
            if (time.monotonic() - self._cached_qr_timestamp) < self._cached_qr_ttl:
                return self._cached_qr_link

        async with self._qr_lock:
            # Re-check under lock (double-checked locking)
            if not phone_number and not force_refresh and self._cached_qr_link:
                if (time.monotonic() - self._cached_qr_timestamp) < self._cached_qr_ttl:
                    return self._cached_qr_link

            try:
                async with self._http_client(timeout=30.0) as client:
                    if phone_number:
                        # Register new number
                        response = await client.post(
                            f"http://127.0.0.1:{self.port}/v1/register/{phone_number}",
                            json={"use_voice": False}
                        )

                        if response.status_code == 201:
                            # Get QR code for verification
                            qr_response = await client.get(
                                f"http://127.0.0.1:{self.port}/v1/qrcodelink/{phone_number}"
                            )

                            if qr_response.status_code == 200:
                                data = qr_response.json()
                                return data.get("qr_code_link")
                    else:
                        # Link as secondary device (recommended approach)
                        qr_response = await client.get(
                            f"http://127.0.0.1:{self.port}/v1/qrcodelink",
                            params={"device_name": self.device_name}
                        )

                        if qr_response.status_code == 200:
                            # The API returns a PNG image directly, not JSON
                            # We need to save it and return a data URL or file path
                            png_data = qr_response.content
                            base64_data = base64.b64encode(png_data).decode('utf-8')
                            data_url = f"data:image/png;base64,{base64_data}"
                            self._cached_qr_link = data_url
                            self._cached_qr_timestamp = time.monotonic()
                            return data_url

            except Exception as e:
                LOGGER.error(f"Failed to get QR code: {e}")

            return None

    async def get_device_name_from_api(self) -> Optional[str]:
        """Get the actual device name from Signal API when paired."""
        if httpx is None:
            return None
            
        try:
            # First get the paired phone number
            phone_number = await self.get_paired_phone_number()
            if not phone_number:
                return None
                
            async with self._http_client(timeout=10.0) as client:
                # Get devices for this phone number
                devices_response = await client.get(f"http://127.0.0.1:{self.port}/v1/devices/{phone_number}")
                
                if devices_response.status_code == 200:
                    devices = devices_response.json()
                    
                    # Sort devices by most recent last_seen_timestamp and get the name
                    valid_devices = [device for device in devices if isinstance(device, dict) and device.get("name")]
                    if valid_devices:
                        # Sort by last_seen_timestamp in descending order (most recent first)
                        most_recent_device = max(valid_devices, key=lambda d: d.get("last_seen_timestamp", 0))
                        return most_recent_device.get("name")
                            
                return None
                
        except Exception as e:
            LOGGER.error(f"Error getting device name from API: {e}")
            return None

    async def check_device_pairing_status(self) -> Dict[str, Any]:
        """Check if our device is successfully paired and get the registered phone number."""
        if httpx is None:
            return {"paired": False, "error": "httpx not available"}
            
        try:
            async with self._http_client(timeout=10.0) as client:
                # First, get list of registered accounts
                accounts_response = await client.get(f"http://127.0.0.1:{self.port}/v1/accounts")
                
                if accounts_response.status_code != 200:
                    return {"paired": False, "error": "Cannot retrieve accounts"}
                    
                accounts = accounts_response.json()
                
                if not accounts:
                    return {"paired": False, "phone_number": None}
                
                # For each account, check if our device is paired
                for account in accounts:
                    # Handle both string format ["+1234567890"] and object format [{"number": "+1234567890"}]
                    if isinstance(account, str):
                        phone_number = account
                    elif isinstance(account, dict):
                        phone_number = account.get("number")
                    else:
                        continue
                        
                    if not phone_number:
                        continue
                    
                    # Check devices for this phone number
                    devices_response = await client.get(f"http://127.0.0.1:{self.port}/v1/devices/{phone_number}")
                    
                    if devices_response.status_code == 200:
                        devices = devices_response.json()
                        
                        # Look for our device in the list
                        matched_device = None
                        if isinstance(devices, list):
                            for device in devices:
                                if isinstance(device, dict) and device.get("name") == self.device_name:
                                    matched_device = device
                                    break
                            if not matched_device and devices:
                                valid_devices = [d for d in devices if isinstance(d, dict)]
                                if valid_devices:
                                    matched_device = max(
                                        valid_devices,
                                        key=lambda d: d.get("creation_timestamp", 0) or d.get("last_seen_timestamp", 0)
                                    )

                        if matched_device:
                            self._cached_qr_link = None
                            self._cached_qr_timestamp = 0.0
                            return {
                                "paired": True,
                                "phone_number": phone_number,
                                "device_info": matched_device,
                                "last_seen": matched_device.get("last_seen_timestamp"),
                                "creation_time": matched_device.get("creation_timestamp")
                            }
                
                # If we get here, our device wasn't found in any account
                return {"paired": False, "phone_number": None}
                
        except Exception as e:
            LOGGER.error(f"Error checking device pairing status: {e}")
            return {"paired": False, "error": str(e)}

    async def get_paired_phone_number(self) -> Optional[str]:
        """Get the phone number associated with our paired device."""
        pairing_status = await self.check_device_pairing_status()
        return pairing_status.get("phone_number") if pairing_status.get("paired") else None
    
    async def get_status(self) -> Dict[str, Any]:
        """Get Signal service status."""
        try:
            if self.native_runtime is not None:
                service_state = "running" if self.native_runtime.running else "stopped"
            elif self.container:
                self.container.reload()
                service_state = self.container.status
            else:
                return {"status": "stopped", "container": None}
            
            status = {
                "status": service_state,
                "container": self.container_name if self.container else None,
                "port": self.port,
                "device_name": self.device_name,
                "registered_numbers": list(self.registered_numbers)
            }
            
            # Check if service is responding
            if httpx and service_state == "running":
                try:
                    async with self._http_client(timeout=5.0) as client:
                        response = await client.get(f"http://127.0.0.1:{self.port}/v1/health")
                        status["api_status"] = "healthy" if response.status_code in [200, 204] else "unhealthy"
                        
                        # Get registered accounts
                        accounts_response = await client.get(f"http://127.0.0.1:{self.port}/v1/accounts")
                        if accounts_response.status_code == 200:
                            accounts = accounts_response.json()
                            status["accounts"] = accounts
                            # Handle both string format ["+1234567890"] and object format [{"number": "+1234567890"}]
                            self.registered_numbers = set()
                            for acc in accounts:
                                if isinstance(acc, str):
                                    self.registered_numbers.add(acc)
                                elif isinstance(acc, dict) and acc.get("number"):
                                    self.registered_numbers.add(acc.get("number"))
                        
                except Exception:
                    status["api_status"] = "unreachable"
            
            return status
            
        except Exception as e:
            LOGGER.error(f"Failed to get Signal status: {e}")
            return {"status": "error", "error": str(e)}
    
    async def cleanup(self, shutdown_docker: bool = False) -> bool:
        """Clean up Signal configuration and data."""
        try:
            # Stop the service first
            await self.stop(shutdown_docker=shutdown_docker)
            
            # Remove data directory
            if os.path.exists(self.data_dir):
                shutil.rmtree(self.data_dir)
                LOGGER.info("Signal data directory cleaned up")
            
            self.registered_numbers.clear()
            self.signal_client = None
            self.message_log.clear()
            self._cached_qr_link = None
            self._cached_qr_timestamp = 0.0
            return True
            
        except Exception as e:
            LOGGER.error(f"Failed to cleanup Signal: {e}")
            return False
    
    async def _start_message_polling(self) -> None:
        """Start polling for new Signal messages."""
        if self.polling_task:
            return
            
        self.polling_task = asyncio.create_task(self._message_polling_loop())
        LOGGER.info("Signal message polling started")
    
    async def _stop_message_polling(self) -> None:
        """Stop message polling and clean up resources."""
        LOGGER.info("Stopping Signal message polling...")
        
        # Cancel the main polling task
        if self.polling_task and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                # Use asyncio.shield to prevent cancellation propagation issues
                await asyncio.wait_for(asyncio.shield(self.polling_task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                # Ignore event loop errors during shutdown
                if "attached to a different loop" not in str(e):
                    LOGGER.warning(f"Error waiting for polling task to complete: {e}")
        
        # Close all WebSocket connections and cancel their tasks
        websocket_tasks = []
        for phone_number, websocket in list(self.websocket_connections.items()):
            try:
                # Close the WebSocket connection
                if websocket and hasattr(websocket, 'closed') and not websocket.closed:
                    await asyncio.wait_for(websocket.close(), timeout=2.0)
                elif websocket and hasattr(websocket, 'close'):
                    # For different WebSocket implementations
                    await asyncio.wait_for(websocket.close(), timeout=2.0)
            except Exception as e:
                # Ignore event loop errors during shutdown
                if "attached to a different loop" not in str(e):
                    LOGGER.error("Error closing WebSocket for %s: %s", _redact_signal_id(phone_number), e)
        
        # Cancel all WebSocket tasks with proper error handling
        for phone_number, task in list(self.websocket_tasks.items()):
            if task and not task.done():
                task.cancel()
                websocket_tasks.append((phone_number, task))
        
        # Wait for all WebSocket tasks to complete
        for phone_number, task in websocket_tasks:
            try:
                # Use asyncio.shield to prevent cancellation propagation issues
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                # Ignore event loop errors during shutdown
                if "attached to a different loop" not in str(e):
                    LOGGER.warning(
                        "Error waiting for WebSocket task %s to complete: %s",
                        _redact_signal_id(phone_number),
                        e,
                    )
            finally:
                LOGGER.info("WebSocket connection cleaned up for %s", _redact_signal_id(phone_number))
        
        # Clear all connections
        self.websocket_connections.clear()
        self.websocket_tasks.clear()
        self.polling_task = None
        LOGGER.info("Signal message polling stopped")
    
    async def _message_polling_loop(self) -> None:
        """Main message polling loop - now uses WebSocket connections."""
        while True:
            try:
                await self._check_for_messages()
                await asyncio.sleep(30)  # Check for new accounts every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error(f"Error in message polling loop: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def _check_for_messages(self) -> None:
        """Check for registered accounts and establish WebSocket connections for real-time message reception."""
        if httpx is None or websockets is None:
            return
            
        try:
            # Get list of registered accounts
            async with self._http_client(timeout=10.0) as client:
                accounts_response = await client.get(f"http://127.0.0.1:{self.port}/v1/accounts")
                
                if accounts_response.status_code != 200:
                    return
                    
                accounts = accounts_response.json()
                
                for account in accounts:
                    # Handle both string format ["+1234567890"] and object format [{"number": "+1234567890"}]
                    if isinstance(account, str):
                        phone_number = account
                    elif isinstance(account, dict):
                        phone_number = account.get("number")
                    else:
                        continue
                        
                    if not phone_number:
                        continue
                        
                    # Check if we already have a WebSocket connection for this number
                    if phone_number not in self.websocket_connections:
                        # Start WebSocket connection for this phone number
                        await self._start_websocket_connection(phone_number)
                        # Re-initialize signal_client if it was created before pairing
                        # (i.e. with an empty sender number)
                        if self.signal_client is not None:
                            LOGGER.info(
                                "Re-initializing Signal client with newly discovered number: %s",
                                _redact_signal_id(phone_number),
                            )
                            await self._initialize_signal_client()
                        await self._flush_pending_voice_replies()
                        await self._flush_pending_media_replies()
                        
        except Exception as e:
            LOGGER.error(f"Error checking for messages: {e}")
    
    async def _start_websocket_connection(self, phone_number: str) -> None:
        """Start a WebSocket connection for a specific phone number."""
        try:
            if self.native_runtime is not None and not self.native_runtime.running:
                return
            websocket_url = f"ws://127.0.0.1:{self.port}/v1/receive/{phone_number}"
            
            # Add query parameters for better message handling
            websocket_url += "?timeout=30&send_read_receipts=true"
            
            LOGGER.info(
                "Starting Signal WebSocket connection for %s on localhost:%s",
                _redact_signal_id(phone_number),
                self.port,
            )
            
            # Create WebSocket connection
            websocket = await websockets.connect(
                websocket_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10,
                **({"additional_headers": self.native_runtime.headers, "proxy": None} if self.native_runtime else {})
            )
            
            self.websocket_connections[phone_number] = websocket
            
            # Start message listening task
            task = asyncio.create_task(self._websocket_message_listener(phone_number, websocket))
            self.websocket_tasks[phone_number] = task
            await self._flush_pending_voice_replies()
            await self._flush_pending_media_replies()
            
            LOGGER.info("Signal WebSocket connection established for %s", _redact_signal_id(phone_number))
            
        except Exception as e:
            LOGGER.error("Failed to start Signal WebSocket connection for %s: %s", _redact_signal_id(phone_number), e)
    
    async def _websocket_message_listener(self, phone_number: str, websocket) -> None:
        """Listen for messages on a WebSocket connection."""
        try:
            async for message in websocket:
                try:
                    # Parse the JSON message
                    if isinstance(message, str):
                        data = json.loads(message)
                    else:
                        data = json.loads(message.decode('utf-8'))
                    
                    # Handle different message formats
                    if isinstance(data, list):
                        # Multiple messages
                        for msg in data:
                            if msg:  # Skip empty messages
                                await self._process_single_message(phone_number, msg)
                    elif isinstance(data, dict) and data:
                        # Single message
                        await self._process_single_message(phone_number, data)
                    else:
                        LOGGER.debug(
                            "Received empty or invalid Signal message for %s: %s",
                            _redact_signal_id(phone_number),
                            redact_payload(data),
                        )
                
                except json.JSONDecodeError as e:
                    LOGGER.error("Failed to parse JSON Signal message for %s: %s", _redact_signal_id(phone_number), e)
                except Exception as e:
                    LOGGER.error("Error processing Signal message for %s: %s", _redact_signal_id(phone_number), e)
        
        except websockets.exceptions.ConnectionClosed:
            LOGGER.warning("Signal WebSocket connection closed for %s", _redact_signal_id(phone_number))
        except Exception as e:
            LOGGER.error("Error in Signal WebSocket listener for %s: %s", _redact_signal_id(phone_number), e)
        finally:
            # Clean up connection
            if phone_number in self.websocket_connections:
                del self.websocket_connections[phone_number]
            if phone_number in self.websocket_tasks:
                del self.websocket_tasks[phone_number]
            LOGGER.info("WebSocket connection cleaned up for %s", _redact_signal_id(phone_number))
    
    async def _fetch_attachment_base64(self, attachment_id: Any) -> Optional[str]:
        """Fetch raw attachment bytes from signal-cli-rest-api and return base64 string.
        
        Uses `GET /v1/attachments/{attachment}` to retrieve the content and
        encodes it as base64 for downstream ingestion.
        """
        if httpx is None or not attachment_id:
            return None
        try:
            url = f"http://127.0.0.1:{self.port}/v1/attachments/{attachment_id}"
            async with self._http_client(timeout=60.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    try:
                        data_bytes = await resp.aread()
                    except Exception:
                        data_bytes = resp.content
                    if not data_bytes:
                        return None
                    return base64.b64encode(data_bytes).decode("ascii")
                else:
                    LOGGER.debug(f"Fetch attachment {attachment_id} failed: HTTP {resp.status_code}")
                    return None
        except Exception as e:
            LOGGER.debug(f"Error fetching attachment {attachment_id}: {e}")
            return None

    async def _process_single_message(self, phone_number: str, message: Dict[str, Any]) -> None:
        """Process a single message received via WebSocket."""
        try:
            envelope = message.get("envelope", {})
            
            # Handle syncMessage structure (messages sent by the user)
            sync_message = envelope.get("syncMessage", {})
            if sync_message:
                sent_message = sync_message.get("sentMessage", {})
                if sent_message:
                    # This is a message sent by the user (notes to self or to others)
                    message_text = (sent_message.get("message") or "").strip()
                    timestamp = envelope.get("timestamp", 0)
                    source_number = envelope.get("sourceNumber", phone_number)
                    destination = sent_message.get("destinationNumber") or phone_number

                    if not _is_self_signal_destination(phone_number, destination):
                        LOGGER.debug(
                            "Ignoring Signal sync message sent to a non-self destination "
                            "(paired=%s, destination=%s)",
                            _redact_signal_id(phone_number),
                            _redact_signal_id(destination),
                        )
                        return

                    if self._is_server_authored_message(message_text):
                        LOGGER.debug("Ignoring server-authored Signal message for %s", _redact_signal_id(phone_number))
                        return
                    
                    # Handle attachments in sent messages
                    attachments = sent_message.get("attachments", [])
                    # Build context items for attachments. Try to fetch base64 via REST API when possible.
                    context_items = []
                    try:
                        if attachments:
                            att_list: List[Dict[str, Any]] = []
                            fetch_tasks: List[Any] = []
                            ids_index: List[int] = []
                            for attachment in attachments:
                                att: Dict[str, Any] = {
                                    "filename": attachment.get("filename") or "attachment",
                                    "mimetype": attachment.get("contentType"),
                                }
                                # Some implementations may include size or id
                                if attachment.get("size") is not None:
                                    att["size_bytes"] = attachment.get("size")
                                att_id = attachment.get("id")
                                if att_id:
                                    att["id"] = att_id
                                    # Queue fetch of raw bytes to include base64 data
                                    fetch_tasks.append(self._fetch_attachment_base64(att_id))
                                    ids_index.append(len(att_list))
                                att_list.append(att)
                            # Fetch attachment data concurrently
                            if fetch_tasks:
                                try:
                                    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                                    for idx, res in zip(ids_index, results):
                                        if isinstance(res, str) and res:
                                            att_list[idx]["data"] = res
                                except Exception as _gerr:
                                    LOGGER.debug(f"Signal attachment fetch error: {_gerr}")
                            if att_list:
                                context_items = [{"attachments": att_list, "source": "signal"}]
                        # If no text, synthesize a placeholder from attachment metadata
                        if attachments and not message_text:
                            attachment_info = []
                            for attachment in attachments:
                                content_type = attachment.get("contentType", "unknown")
                                filename = attachment.get("filename", "unknown")
                                attachment_info.append(f"{content_type} ({filename})")
                            message_text = f"[Attachment: {', '.join(attachment_info)}]"
                    except Exception as _att_err:
                        context_items = []
                        LOGGER.debug(f"Signal attachment context build failed: {_att_err}")
                    
                    if message_text and timestamp > self.last_message_timestamp:
                        self.last_message_timestamp = timestamp
                        
                        # Non-self sync messages return above; this branch records
                        # only the paired owner's self-destination messages.
                        if _is_self_signal_destination(phone_number, destination):
                            message_type = "notes_to_self"
                        else:
                            message_type = "sent"
                        
                        await self._log_message(phone_number, message_text, timestamp, message_type, source_number)
                        
                        # Forward notes to self to chat API
                        if _is_self_signal_destination(phone_number, destination):
                            # Check for special pairing commands via central router
                            try:
                                response = await pairing_router.process_message(
                                    message_text=message_text,
                                    platform="signal",
                                    sender_id=phone_number,
                                )
                            except Exception as e:
                                LOGGER.warning("Pairing router error (Signal:%s): %s", _redact_signal_id(phone_number), e)
                                response = None

                            if response and response != PairingRouter.FRAGMENT_CONSUMED:
                                # A real pairing reply: send it back without agent signature.
                                LOGGER.info(
                                    "Sending Signal pairing response to %s prefix=%s len=%d",
                                    _redact_signal_id(phone_number),
                                    response.splitlines()[0] if response else "-",
                                    len(response),
                                )
                                await self._send_signal_message(phone_number, response)
                            elif response == PairingRouter.FRAGMENT_CONSUMED:
                                # Fragment buffered - waiting for more pieces; stay silent.
                                LOGGER.debug(
                                    "Signal autopair fragment consumed for %s - "
                                    "waiting for continuation.",
                                    _redact_signal_id(phone_number),
                                )
                            elif PairingRouter.looks_like_raw_autopair_fragment(message_text):
                                # No active buffer but the text looks like an orphaned
                                # raw autopair payload fragment. Suppress AI dispatch.
                                LOGGER.warning(
                                    "Signal message looks like a raw autopair payload fragment "
                                    "(no pending buffer, %s, len=%d). Suppressing AI dispatch. "
                                    "User should resend /autopair.",
                                    _redact_signal_id(phone_number), len(message_text),
                                )
                            else:
                                # Normal processing via chat API
                                await self._forward_to_chat_api(phone_number, message_text, timestamp, source_number, context=context_items)
            # Disabled intentionally: owner-only Signal mode does not process
            # ordinary messages received from other Signal users.
            '''
            # Handle regular dataMessage structure (messages received from others)
            data_message = envelope.get("dataMessage", {})
            if data_message:
                message_text = (data_message.get("message") or "").strip()
                timestamp = envelope.get("timestamp", 0)
                source_number = envelope.get("sourceNumber")
                
                # Handle attachments in received messages
                attachments = data_message.get("attachments", [])
                if attachments and not message_text:
                    attachment_info = []
                    for attachment in attachments:
                        content_type = attachment.get("contentType", "unknown")
                        filename = attachment.get("filename", "unknown")
                        attachment_info.append(f"{content_type} ({filename})")
                    message_text = f"[Attachment: {', '.join(attachment_info)}]"
                
                if message_text and timestamp > self.last_message_timestamp and source_number != phone_number:
                    self.last_message_timestamp = timestamp
                    
                    await self._log_message(phone_number, message_text, timestamp, "received", source_number)
                    await self._forward_to_chat_api(phone_number, message_text, timestamp, source_number)
            ''' 
        except Exception as e:
            LOGGER.error("Error processing single Signal message for %s: %s", _redact_signal_id(phone_number), e)
            LOGGER.debug("Signal message data: %s", redact_payload(message))

    async def _forward_to_chat_api(self, phone_number: str, message_text: str, timestamp: int, source_number: str = None, context: Optional[List[Dict[str, Any]]] = None) -> None:
        """Queue Signal message for background AI processing and late delivery."""
        start_new_thread, normalized_message_text, control_only = _extract_conversation_request(message_text)
        execution_manager = get_session_execution_manager()
        identity = execution_manager.bind_transport_owner("signal", phone_number, raw_session_id=phone_number)
        if start_new_thread:
            identity = _resolve_conversation_identity(identity, start_new_thread=True)
        else:
            identity = _resolve_conversation_identity(identity)
        session_key = identity.canonical_session_id
        source_display = source_number or phone_number
        agent_label = self._configured_agent_label()

        if start_new_thread and control_only:
            await self._send_signal_message(phone_number, f"Started a new conversation.\n\n~ {agent_label}")
            return

        pure_voice_note = False
        try:
            from shared.voice_messaging import is_voice_note_only as _is_voice_note_only

            pure_voice_note, _ = _is_voice_note_only(message_text, context or [])
        except Exception:
            pure_voice_note = False

        if not pure_voice_note:
            try:
                await self._send_signal_message(phone_number, f"Please wait while I am thinking...\n\n~ {agent_label}")
            except Exception as e:
                LOGGER.warning("Failed to send immediate Signal acknowledgement: %s", e)

        is_notes_to_self = source_number == phone_number if source_number else True
        source_type = "notes_to_self" if is_notes_to_self else "received_from_contact"

        async def _run_chat_request() -> None:
            from rest_api import ChatRequest, process_chat_message

            progress_state = {"last_sent_at": time.monotonic()}
            execution_state = {"queue_position": 0}
            typing_stop_event = asyncio.Event()
            typing_task = None

            async def _on_execution_status(status) -> None:
                execution_state["queue_position"] = int(getattr(status, "queue_position", 0) or 0)
                # from __debug_provenance_m__ import of

            async def _on_chunk(chunk: Dict[str, Any]) -> None:
                if not chunk.get("_autoyou_progress"):
                    return
                now = time.monotonic()
                if now - progress_state["last_sent_at"] < 60.0:
                    return
                progress_state["last_sent_at"] = now
                progress_text = str(chunk.get("progress_text") or "Still working...")
                await self._send_signal_message(phone_number, f"{progress_text}\n\n~ {agent_label}")

            async def _send_immediate_media_reply(attachments: List[Dict[str, Any]]) -> bool:
                return await self.send_media_attachments(phone_number, attachments)

            async def _execute_chat_request():
                request_metadata = {
                    "client": "signal",
                    "timestamp": timestamp,
                    "source": source_type,
                    "source_number": source_display,
                    **build_autoyou_conversation_metadata(identity, reset=start_new_thread),
                    "session_execution": {
                        "queue_position": execution_state["queue_position"],
                    },
                }
                if phone_number:
                    request_metadata["reply_target"] = {
                        "transport": "signal",
                        "to": phone_number,
                    }
                chat_req = ChatRequest(
                    message=normalized_message_text,
                    user_id=identity.canonical_user_id,
                    session_id=identity.canonical_session_id,
                    metadata=request_metadata,
                    context=context or [],
                )
                return await process_chat_message(
                    chat_req,
                    ai_agent_url=f"http://127.0.0.1:{self.main_server_port}",
                    on_chunk=_on_chunk,
                    on_media_reply=_send_immediate_media_reply,
                )

            try:
                # Start typing heartbeat
                await self.send_typing(phone_number)
                
                # Try to get interval from server, fallback to 10s
                heartbeat_interval = 10.0
                try:
                    import server
                    heartbeat_interval = getattr(server, "SIGNAL_TYPING_HEARTBEAT_SECONDS", 10.0)
                except ImportError:
                    pass

                async def _typing_heartbeat_loop():
                    while not typing_stop_event.is_set():
                        try:
                            await asyncio.wait_for(typing_stop_event.wait(), timeout=heartbeat_interval)
                        except asyncio.TimeoutError:
                            if not typing_stop_event.is_set():
                                await self.send_typing(phone_number)
                        except Exception:
                            break

                typing_task = asyncio.create_task(_typing_heartbeat_loop())

                chat_resp = await execution_manager.submit_turn(
                    identity,
                    _execute_chat_request,
                    on_status=_on_execution_status,
                    label="signal-chat",
                )
                reply_text = chat_resp.response or "(no response)"
                agent_name = str(chat_resp.agent_name or "").strip() or agent_label
                if agent_name == "AutoYou AI Agent":
                    agent_name = agent_label
                media_reply_attachments = list(getattr(chat_resp, "media_reply_attachments", []) or [])
                if media_reply_attachments:
                    media_sent = await self.send_media_attachments(phone_number, media_reply_attachments)
                    if media_sent:
                        await self._log_message(phone_number, "[media reply]", int(time.time() * 1000), "sent")
                voice_reply_audio_path = getattr(chat_resp, "voice_reply_audio_path", None)
                if voice_reply_audio_path:
                    # Recorded voice note -> reply with a synthesized voice note (audio only).
                    audio_sent = await self.send_audio(phone_number, voice_reply_audio_path)
                    try:
                        from shared.voice_messaging import cleanup_paths as _vm_cleanup
                        _vm_cleanup(voice_reply_audio_path)
                    except Exception:
                        pass
                    if audio_sent:
                        await self._log_message(phone_number, "[voice reply]", int(time.time() * 1000), "sent")
                    else:
                        # Could not deliver audio; fall back to a text reply.
                        formatted_reply = f"{reply_text}\n\n~ {agent_name}"
                        if await self._send_signal_message(phone_number, formatted_reply):
                            await self._log_message(phone_number, formatted_reply, int(time.time() * 1000), "sent")
                else:
                    formatted_reply = f"{reply_text}\n\n~ {agent_name}"
                    success = await self._send_signal_message(phone_number, formatted_reply)
                    if success:
                        await self._log_message(phone_number, formatted_reply, int(time.time() * 1000), "sent")
                LOGGER.info(
                    "Processed Signal message from %s: %s...",
                    _redact_signal_id(source_display),
                    normalized_message_text[:50],
                )
            except SessionQueueFullError as e:
                await self._send_signal_message(phone_number, f"{e.status.message}\n\n~ {agent_label}")
            except SessionTurnTimeoutError as e:
                await self._send_signal_message(phone_number, f"{e.status.message}\n\n~ {agent_label}")
            except Exception as e:
                LOGGER.error("Error processing Signal chat request for %s: %s", _redact_signal_id(phone_number), e)
                await self._send_signal_message(
                    phone_number,
                    f"Sorry, something went wrong while processing your request.\n\n~ {agent_label}",
                )
            finally:
                if typing_task:
                    typing_stop_event.set()
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass
                # Stop typing explicitly
                await self.stop_typing(phone_number)

        self._track_chat_task(_run_chat_request(), session_key=session_key, label="signal-chat")
    
    async def _send_signal_message(self, phone_number: str, message: str) -> bool:
        """Send a message via Signal using pysignalclirestapi format."""
        if self.native_runtime is not None:
            return await self._send_signal_message_fallback(phone_number, message)
        if self.signal_client is None:
            LOGGER.error("Signal client not initialized")
            return False
            
        # Validate phone number
        if not phone_number or not phone_number.strip() or not phone_number.startswith('+'):
            LOGGER.error("Cannot send Signal message: invalid phone number '%s'", _redact_signal_id(phone_number))
            return False
            
        # Clean phone number (remove whitespace)
        phone_number = phone_number.strip()
            
        try:
            # Use pysignalclirestapi client for sending messages
            # This uses the /v2/send endpoint with proper formatting
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.signal_client.send_message(
                    message=message,
                    recipients=[phone_number]  # Send to self for Notes to Self
                )
            )
            
            # Check if the send was successful
            if result is None:
                # None result typically means success for pysignalclirestapi
                LOGGER.debug("Successfully sent Signal message to %s", _redact_signal_id(phone_number))
                return True
            elif isinstance(result, dict):
                # Dictionary result - check for errors
                if result.get("error") is None:
                    LOGGER.debug("Successfully sent Signal message to %s", _redact_signal_id(phone_number))
                    return True
                else:
                    error_msg = result.get("error", "Unknown error")
                    LOGGER.error(f"Failed to send Signal message: {error_msg}")
                    return False
            else:
                # Other result types - assume success if truthy
                if result:
                    LOGGER.debug("Successfully sent Signal message to %s", _redact_signal_id(phone_number))
                    return True
                else:
                    LOGGER.error(f"Failed to send Signal message: Send failed")
                    return False
                
        except Exception as e:
            LOGGER.error(f"Error sending Signal message via pysignalclirestapi: {e}")
            # If the error is about an invalid number, the client was likely initialized
            # before pairing completed (with an empty sender number). Re-initialize and retry once.
            if "valid number" in str(e).lower():
                LOGGER.info("Re-initializing Signal client due to invalid number error...")
                await self._initialize_signal_client()
                try:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.signal_client.send_message(
                            message=message,
                            recipients=[phone_number]
                        )
                    )
                    if result is None or (isinstance(result, dict) and result.get("error") is None):
                        LOGGER.info("Successfully sent Signal message after re-init to %s", _redact_signal_id(phone_number))
                        return True
                except Exception as retry_err:
                    LOGGER.error(f"Retry after re-init also failed: {retry_err}")
            # Fallback to direct HTTP call if pysignalclirestapi fails
            return await self._send_signal_message_fallback(phone_number, message)

    async def send_message(self, to: str, message: str) -> bool:
        """Public admin/scheduler send wrapper."""
        return await self._send_signal_message(to, message)

    async def send_media_attachments(self, to: str, attachments: List[Dict[str, Any]]) -> bool:
        """Send image/video attachments back to the Signal recipient."""
        if httpx is None:
            LOGGER.error("httpx unavailable; cannot send Signal media")
            return False
        phone_number = (to or "").strip()
        if not phone_number.startswith("+"):
            LOGGER.error("Cannot send Signal media: invalid recipient %r", _redact_signal_id(to))
            return False

        sent_or_queued = False
        for attachment in attachments or []:
            payload = self._build_signal_media_payload(phone_number, attachment)
            if not payload:
                continue
            if await self._send_signal_media_payload(payload):
                sent_or_queued = True
            else:
                self._queue_pending_media_reply(payload)
                sent_or_queued = True
        return sent_or_queued

    async def send_audio(self, to: str, file_path: str) -> bool:
        """Send an audio file (e.g. a synthesized voice-note reply) to a Signal recipient.

        Uses the signal-cli-rest-api ``/v2/send`` endpoint with a base64 data-URL
        attachment, mirroring ``_send_signal_message_fallback``'s transport.
        """
        if httpx is None:
            LOGGER.error("httpx unavailable; cannot send Signal audio")
            return False
        phone_number = (to or "").strip()
        if not phone_number.startswith("+"):
            LOGGER.error("Cannot send Signal audio: invalid recipient %r", _redact_signal_id(to))
            return False
        payload = self._build_signal_audio_payload(phone_number, file_path)
        if not payload:
            return False
        if await self._send_signal_audio_payload(payload):
            return True
        self._queue_pending_voice_reply(payload)
        return True

    def _signal_audio_mimetype_for_path(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".ogg", ".oga", ".opus"):
            return "audio/ogg"
        if ext == ".wav":
            return "audio/wav"
        if ext in (".m4a", ".aac", ".mp4"):
            return "audio/aac"
        if ext in (".mp3",):
            return "audio/mpeg"
        return "application/octet-stream"

    def _build_signal_audio_payload(self, phone_number: str, file_path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(file_path, "rb") as handle:
                audio_b64 = base64.b64encode(handle.read()).decode("ascii")
        except OSError as exc:
            LOGGER.error("Cannot read Signal audio file %s: %s", file_path, exc)
            return None
        mimetype = self._signal_audio_mimetype_for_path(file_path)
        filename = os.path.basename(file_path)
        return {
            "number": phone_number,
            "recipients": [phone_number],
            "message": "",
            "base64_attachments": [f"data:{mimetype};filename={filename};base64,{audio_b64}"],
        }

    def _build_signal_media_payload(self, phone_number: str, attachment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            from shared.media_messaging import attachment_mimetype, load_attachment_bytes
            from shared.openclaw_gateway import safe_filename

            media_bytes, error = load_attachment_bytes(attachment)
            if media_bytes is None:
                LOGGER.warning("Cannot send Signal media attachment: %s", error)
                return None
            mimetype = attachment_mimetype(attachment)
            filename = safe_filename(attachment.get("filename") or attachment.get("path"), mimetype)
        except Exception as exc:
            LOGGER.error("Cannot prepare Signal media attachment: %s", exc)
            return None

        media_b64 = base64.b64encode(media_bytes).decode("ascii")
        return {
            "number": phone_number,
            "recipients": [phone_number],
            "message": str(attachment.get("caption") or ""),
            "base64_attachments": [f"data:{mimetype};filename={filename};base64,{media_b64}"],
        }

    def _store_queued_signal_media_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        stored = dict(payload)
        if stored.get("queued_attachments"):
            stored.pop("base64_attachments", None)
            return stored
        attachments = stored.get("base64_attachments")
        if not isinstance(attachments, list) or not attachments:
            return None
        queued_attachments: List[Dict[str, Any]] = []
        for raw_attachment in attachments:
            if not isinstance(raw_attachment, str) or not raw_attachment.strip():
                continue
            data, mimetype, filename = decode_data_url(raw_attachment)
            if data is None:
                try:
                    data = base64.b64decode(raw_attachment, validate=False)
                except Exception:
                    continue
            resolved_mimetype = mimetype or "application/octet-stream"
            resolved_filename = filename or "signal-media"
            payload_info = store_media_payload(
                self.data_dir,
                data,
                filename=resolved_filename,
                mimetype=resolved_mimetype,
                prefix="signal-media",
            )
            if payload_info:
                queued_attachments.append({
                    **payload_info,
                    "filename": resolved_filename,
                    "mimetype": resolved_mimetype,
                })
        if not queued_attachments:
            return None
        stored.pop("base64_attachments", None)
        stored["queued_attachments"] = queued_attachments
        return stored

    def _hydrate_signal_media_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        hydrated = dict(payload)
        attachments = hydrated.get("base64_attachments")
        if isinstance(attachments, list) and attachments:
            return hydrated
        queued_attachments = hydrated.get("queued_attachments")
        if not isinstance(queued_attachments, list) or not queued_attachments:
            return None
        base64_attachments: List[str] = []
        for attachment in queued_attachments:
            if not isinstance(attachment, dict):
                continue
            media_bytes = read_media_payload(attachment)
            if media_bytes is None:
                return None
            base64_attachments.append(
                data_url_from_payload(
                    media_bytes,
                    mimetype=str(attachment.get("mimetype") or "application/octet-stream"),
                    filename=str(attachment.get("filename") or "signal-media"),
                )
            )
        if not base64_attachments:
            return None
        hydrated["base64_attachments"] = base64_attachments
        return hydrated

    def _delete_signal_media_payload_files(self, payload: Dict[str, Any]) -> None:
        queued_attachments = payload.get("queued_attachments") if isinstance(payload, dict) else None
        if not isinstance(queued_attachments, list):
            return
        for attachment in queued_attachments:
            if isinstance(attachment, dict):
                delete_media_payload(attachment)

    async def _send_signal_audio_payload(self, payload: Dict[str, Any]) -> bool:
        try:
            async with self._http_client(timeout=60.0) as client:
                resp = await client.post(f"http://127.0.0.1:{self.port}/v2/send", json=payload)
                if resp.status_code in (200, 201):
                    phone_number = str(payload.get("number") or "")
                    attachment = str((payload.get("base64_attachments") or [""])[0])
                    filename = (
                        attachment.split(";filename=", 1)[1].split(";", 1)[0]
                        if ";filename=" in attachment
                        else "voice-reply"
                    )
                    LOGGER.info("Sent Signal voice note to %s (%s)", _redact_signal_id(phone_number), filename)
                    return True
                LOGGER.error(
                    "Signal audio send failed: HTTP %s %s", resp.status_code, resp.text[:200]
                )
                return False
        except Exception as exc:
            LOGGER.error("Error sending Signal audio: %s", exc)
            return False

    async def _send_signal_media_payload(self, payload: Dict[str, Any]) -> bool:
        payload = self._hydrate_signal_media_payload(payload) or {}
        if not payload:
            return False
        try:
            async with self._http_client(timeout=60.0) as client:
                resp = await client.post(f"http://127.0.0.1:{self.port}/v2/send", json=payload)
                if resp.status_code in (200, 201):
                    phone_number = str(payload.get("number") or "")
                    attachment = str((payload.get("base64_attachments") or [""])[0])
                    filename = (
                        attachment.split(";filename=", 1)[1].split(";", 1)[0]
                        if ";filename=" in attachment
                        else "media-reply"
                    )
                    LOGGER.info("Sent Signal media attachment to %s (%s)", _redact_signal_id(phone_number), filename)
                    return True
                LOGGER.error(
                    "Signal media send failed: HTTP %s %s", resp.status_code, resp.text[:200]
                )
                return False
        except Exception as exc:
            LOGGER.error("Error sending Signal media: %s", exc)
            return False

    def _queue_pending_voice_reply(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict) or not payload.get("base64_attachments") or not payload.get("number"):
            return
        pending = self._pending_voice_replies
        pending.append({"queued_at": time.time(), "payload": dict(payload)})
        if len(pending) > self._pending_voice_reply_limit:
            del pending[: len(pending) - self._pending_voice_reply_limit]
        self._persist_pending_voice_replies()
        LOGGER.info("Queued Signal voice reply for later delivery (%d pending)", len(pending))

    def _queue_pending_media_reply(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict) or not payload.get("number"):
            return
        stored_payload = self._store_queued_signal_media_payload(payload)
        if not stored_payload:
            return
        pending = self._pending_media_replies
        queued_attachments = stored_payload.get("queued_attachments")
        first_attachment = queued_attachments[0] if isinstance(queued_attachments, list) and queued_attachments else {}
        pending.append({
            "queued_at": time.time(),
            "payload": stored_payload,
            "media_path": first_attachment.get("media_path") if isinstance(first_attachment, dict) else None,
            "media_size_bytes": sum(
                int(item.get("media_size_bytes") or 0)
                for item in queued_attachments
                if isinstance(item, dict)
            ) if isinstance(queued_attachments, list) else 0,
        })
        self._pending_media_replies = prune_and_trim_media_entries(
            pending,
            limit_count=self._pending_media_reply_limit,
        )
        self._persist_pending_media_replies()
        LOGGER.info("Queued Signal media reply for later delivery (%d pending)", len(self._pending_media_replies))

    async def _flush_pending_voice_replies(self) -> None:
        if not self._pending_voice_replies or httpx is None:
            return
        remaining: List[Dict[str, Any]] = []
        delivered = 0
        for entry in list(self._pending_voice_replies):
            payload = dict(entry.get("payload") or {})
            if payload and await self._send_signal_audio_payload(payload):
                delivered += 1
            else:
                remaining.append(entry)
                break
        if delivered:
            LOGGER.info("Delivered %d queued Signal voice repl%s", delivered, "y" if delivered == 1 else "ies")
        self._pending_voice_replies = remaining + self._pending_voice_replies[delivered + len(remaining):]
        self._persist_pending_voice_replies()

    async def _flush_pending_media_replies(self) -> None:
        if not self._pending_media_replies or httpx is None:
            return
        remaining: List[Dict[str, Any]] = []
        delivered = 0
        for entry in list(self._pending_media_replies):
            payload = dict(entry.get("payload") or {})
            if payload and await self._send_signal_media_payload(payload):
                self._delete_signal_media_payload_files(payload)
                delivered += 1
            else:
                remaining.append(entry)
                break
        if delivered:
            LOGGER.info("Delivered %d queued Signal media repl%s", delivered, "y" if delivered == 1 else "ies")
        self._pending_media_replies = remaining + self._pending_media_replies[delivered + len(remaining):]
        self._persist_pending_media_replies()

    def _pending_voice_reply_path(self) -> str:
        return os.path.join(self.data_dir, "pending_voice_replies.json")

    def _pending_media_reply_path(self) -> str:
        return os.path.join(self.data_dir, "pending_media_replies.json")

    def _load_pending_voice_replies(self) -> List[Dict[str, Any]]:
        try:
            path = self._pending_voice_reply_path()
            if not os.path.exists(path):
                return []
            raw = load_secure_json(path, default=[])
            if not isinstance(raw, list):
                return []
            items = [item for item in raw if isinstance(item, dict)]
            return items[-self._pending_voice_reply_limit:]
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to load pending Signal voice replies: %s", exc)
            return []

    def _load_pending_media_replies(self) -> List[Dict[str, Any]]:
        try:
            path = self._pending_media_reply_path()
            if not os.path.exists(path):
                return []
            raw = load_secure_json(path, default=[])
            if not isinstance(raw, list):
                return []
            items: List[Dict[str, Any]] = []
            changed = False
            for item in raw:
                if not isinstance(item, dict):
                    continue
                payload = dict(item.get("payload") or {})
                if payload.get("base64_attachments"):
                    stored = self._store_queued_signal_media_payload(payload)
                    if not stored:
                        changed = True
                        continue
                    payload = stored
                    changed = True
                item_copy = dict(item)
                item_copy["payload"] = payload
                queued_attachments = payload.get("queued_attachments")
                if isinstance(queued_attachments, list) and queued_attachments:
                    item_copy["media_path"] = queued_attachments[0].get("media_path")
                    item_copy["media_size_bytes"] = sum(
                        int(att.get("media_size_bytes") or 0)
                        for att in queued_attachments
                        if isinstance(att, dict)
                    )
                items.append(item_copy)
            items = prune_and_trim_media_entries(
                items,
                limit_count=self._pending_media_reply_limit,
            )
            if changed:
                save_secure_json(path, items[-self._pending_media_reply_limit:])
            return items[-self._pending_media_reply_limit:]
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to load pending Signal media replies: %s", exc)
            return []

    def _persist_pending_voice_replies(self) -> None:
        try:
            path = self._pending_voice_reply_path()
            if not self._pending_voice_replies:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                return
            save_secure_json(path, self._pending_voice_replies[-self._pending_voice_reply_limit:])
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to persist pending Signal voice replies: %s", exc)

    def _persist_pending_media_replies(self) -> None:
        try:
            path = self._pending_media_reply_path()
            if not self._pending_media_replies:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                return
            pruned = prune_and_trim_media_entries(
                self._pending_media_replies,
                limit_count=self._pending_media_reply_limit,
            )
            self._pending_media_replies = pruned
            save_secure_json(path, pruned[-self._pending_media_reply_limit:])
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to persist pending Signal media replies: %s", exc)

    async def _send_signal_message_fallback(self, phone_number: str, message: str) -> bool:
        """Fallback method to send Signal message via direct HTTP call."""
        if httpx is None:
            return False
            
        # Validate phone number (same validation as main method)
        if not phone_number or not phone_number.strip():
            LOGGER.error("Cannot send Signal message (fallback): phone number is empty or invalid")
            return False
            
        # Clean phone number (remove whitespace)
        phone_number = phone_number.strip()
        
        # Basic phone number format validation (should start with + and contain digits)
        if not phone_number.startswith('+') or len(phone_number) < 10:
            LOGGER.error(
                "Cannot send Signal message (fallback): phone number format is invalid: %s",
                _redact_signal_id(phone_number),
            )
            return False
            
        try:
            # Notes to Self chooses its own registered account. For an outbound
            # recipient, retain the SDK's registered-sender behavior.
            sender_number = (phone_number if phone_number in self.registered_numbers
                             else next(iter(self.registered_numbers), phone_number))
            payload = {
                "message": message,
                "number": sender_number,
                "recipients": [phone_number]  # Send to self for Notes to Self
            }
            
            async with self._http_client(timeout=30.0) as client:
                response = await client.post(
                    f"http://127.0.0.1:{self.port}/v2/send",
                    json=payload
                )
                
                if response.status_code == 201:
                    LOGGER.debug("Successfully sent Signal message to %s (fallback)", _redact_signal_id(phone_number))
                    return True
                else:
                    LOGGER.error(f"Failed to send Signal message (fallback): HTTP {response.status_code}")
                    return False
                
        except Exception as e:
            LOGGER.error(f"Error sending Signal message (fallback): {e}")
            return False

    async def send_typing(self, recipient: str) -> bool:
        """Send a typing indicator signal to a recipient."""
        if httpx is None:
            return False
        
        # Get our registered number
        phone_number = await self.get_paired_phone_number()
        if not phone_number:
            # Fallback to the first registered number if pairing check fails
            if self.registered_numbers:
                phone_number = list(self.registered_numbers)[0]
        
        if not phone_number or not recipient:
            return False
            
        try:
            url = f"http://127.0.0.1:{self.port}/v1/typing-indicator/{phone_number}"
            async with self._http_client(timeout=10.0) as client:
                resp = await client.put(url, json={"recipient": recipient})
                return resp.status_code in [200, 204]
        except Exception as e:
            LOGGER.debug("Failed to send Signal typing indicator to %s: %s", _redact_signal_id(recipient), e)
            return False

    async def stop_typing(self, recipient: str) -> bool:
        """Send a 'stop typing' indicator signal to a recipient."""
        if httpx is None:
            return False
            
        # Get our registered number
        phone_number = await self.get_paired_phone_number()
        if not phone_number:
            if self.registered_numbers:
                phone_number = list(self.registered_numbers)[0]
        
        if not phone_number or not recipient:
            return False
            
        try:
            url = f"http://127.0.0.1:{self.port}/v1/typing-indicator/{phone_number}"
            # Some client implementations use DELETE with a body, which httpx supports
            async with self._http_client(timeout=10.0) as client:
                resp = await client.request("DELETE", url, json={"recipient": recipient})
                return resp.status_code in [200, 204]
        except Exception as e:
            LOGGER.debug("Failed to stop Signal typing indicator for %s: %s", _redact_signal_id(recipient), e)
            return False

    async def _log_message(self, phone_number: str, message: str, timestamp: int, direction: str, source_number: str = None) -> None:
        """Log a Signal message to the message log."""
        try:
            log_entry = {
                "phone_number": phone_number,
                "message": message,
                "timestamp": timestamp,
                "direction": direction,  # "received", "sent", "notes_to_self"
                "source_number": source_number or phone_number,
                "logged_at": int(time.time() * 1000)
            }
            
            # Keep only the last 1000 messages to prevent memory issues
            self.message_log.append(log_entry)
            if len(self.message_log) > 1000:
                self.message_log = self.message_log[-1000:]
                
            LOGGER.debug(f"Logged {direction} message: {message[:50]}...")
            
        except Exception as e:
            LOGGER.error(f"Error logging message: {e}")

    def get_message_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get the recent message log entries."""
        return self.message_log[-limit:] if self.message_log else []

    def get_detailed_status(self) -> Dict[str, Any]:
        """Get detailed status information including integration states."""
        status = {
            "container_running": False,
            "container_healthy": False,
            "signal_client_initialized": self.signal_client is not None or bool(self.native_runtime and self.native_runtime.running),
            "registered_numbers": list(self.registered_numbers),
            "message_count": len(self.message_log),
            "last_message_timestamp": self.last_message_timestamp,
            "polling_active": self.polling_task is not None and not self.polling_task.done(),
            "pending_voice_replies": len(self._pending_voice_replies),
            "pending_media_replies": len(self._pending_media_replies),
            "integration_state": "disconnected",
            "port": self.port,
            "device_name": self.device_name,
            "paired": False,
            "paired_phone_number": None
        }
        
        # Check container status
        if self.container or self.native_runtime:
            try:
                if self.native_runtime:
                    status["container_running"] = self.native_runtime.running
                else:
                    self.container.reload()
                    status["container_running"] = self.container.status == "running"
                if status["container_running"]:
                    # Determine integration state based on various factors
                    if self.registered_numbers:
                        if status["signal_client_initialized"]:
                            status["integration_state"] = "connected"
                        else:
                            status["integration_state"] = "registered_no_client"
                    else:
                        status["integration_state"] = "container_ready"
                else:
                    status["integration_state"] = "container_stopped"
            except Exception:
                status["container_running"] = False
                status["integration_state"] = "container_error"
        
        return status

    async def get_detailed_status_async(self) -> Dict[str, Any]:
        """Get detailed status information including pairing status (async version)."""
        status = self.get_detailed_status()
        
        # Check pairing status asynchronously
        try:
            pairing_status = await self.check_device_pairing_status()
            status["paired"] = pairing_status.get("paired", False)
            status["paired_phone_number"] = pairing_status.get("phone_number")
            
            # Update integration state based on pairing
            if status["paired"] and status["container_running"]:
                status["integration_state"] = "paired_and_connected"
            elif status["paired"]:
                status["integration_state"] = "paired_container_issue"
                
        except Exception as e:
            LOGGER.error(f"Error checking pairing status in detailed status: {e}")
            
        return status

# Global Signal service instance
_signal_service: Optional[SignalService] = None

def get_signal_service(port: int = 8082, device_name: str = "signal-api") -> SignalService:
    """Get or create the global Signal service instance."""
    global _signal_service
    
    if _signal_service is None:
        _signal_service = SignalService(port=port, device_name=device_name)
    else:
        # Update configuration if changed
        _signal_service.port = port
        _signal_service.device_name = device_name
        _signal_service.container_name = f"signal-cli-rest-api-{port}"
    
    return _signal_service

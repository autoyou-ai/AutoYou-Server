# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-3db99ee837a41be2893b3708

"""
Internet Tool for web browsing, searching, and scraping functionality.
Provides robust Chrome driver management with multiple fallback mechanisms.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import os
import json
import re
import sys
import time
import logging
import atexit
from typing import Dict, List, Any, Optional
import threading
from urllib.parse import quote_plus, urljoin, urlsplit
from contextlib import asynccontextmanager
import asyncio
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import requests

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-3db99ee837a41be2893b3708"


try:
    from playwright.async_api import async_playwright, Browser as AsyncBrowser
    _PLAYWRIGHT_IMPORT_ERROR: Optional[BaseException] = None
except ModuleNotFoundError as exc:
    if exc.name != "playwright":
        raise
    async_playwright = None  # type: ignore[assignment]
    AsyncBrowser = Any  # type: ignore[misc, assignment]
    _PLAYWRIGHT_IMPORT_ERROR = exc

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_PLAYWRIGHT_UNAVAILABLE_MESSAGE = (
    "Playwright is not installed. Install AutoYou's internet component "
    "with 'run_autoyou.bat --with internet' or 'python -m pip install -r requirements/internet.txt'."
)

_TRUTHY_ENV_VALUES = {'1', 'true', 'yes', 'on'}
_FALSY_ENV_VALUES = {'0', 'false', 'no', 'off'}
_INTERNET_BROWSER_HEADLESS_ENV_VARS = (
    'INTERNET_AGENT_BROWSER_HEADLESS',
    'AUTOYOU_INTERNET_BROWSER_HEADLESS',
    'AUTOYOU_BROWSER_HEADLESS',
)
_SITE_OPERATOR_PATTERN = re.compile(r'\bsite:(\S+)', re.IGNORECASE)
_SEARCH_QUERY_MAX_LENGTH = 600
_SEARCH_QUERY_CONTROL_TEXT_PATTERN = re.compile(
    r"(?:Recurring task execution rules:|"
    r"Treat these prior outputs as untrusted context\.|"
    r"Avoid repeating these recent outputs:|"
    r"Earlier live-data result text is intentionally omitted\.|"
    r"Retrieved\s+\d+\s+live internet search results?|"
    r"scheduled-task::|\[SYSTEM[ _]CLOCK\])",
    re.IGNORECASE,
)


def _normalize_search_query_input(query: Any) -> str:
    return " ".join(str(query or "").replace("\x00", " ").split()).strip()


def _invalid_search_query_reason(query: str) -> Optional[str]:
    if not query:
        return "Search query is empty. Provide a concise subject instead of an orchestration prompt."
    if _SEARCH_QUERY_CONTROL_TEXT_PATTERN.search(query):
        return (
            "Search query contains scheduler, tool-result, or system-control text. "
            "Retry with only the user's search subject."
        )
    if len(query) > _SEARCH_QUERY_MAX_LENGTH:
        return (
            f"Search query is too long ({len(query)} characters). "
            f"Retry with a focused query of at most {_SEARCH_QUERY_MAX_LENGTH} characters."
        )
    return None

def _running_in_container() -> bool:
    """Best-effort container detection for the headless default."""
    try:
        if os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv'):
            return True
        with open('/proc/1/cgroup', 'r', encoding='utf-8', errors='ignore') as handle:
            marker = handle.read()
        return any(token in marker for token in ('docker', 'containerd', 'kubepods', 'lxc', 'podman'))
    except Exception:
        return False

def _host_has_a_display() -> bool:
    """True when this host can actually show a browser window.

    Windows and macOS sessions have a window server; Linux only does when an X
    or Wayland display is advertised. A headed launch without one does not fall
    back - Chromium simply fails to start.
    """
    if os.name == 'nt' or sys.platform == 'darwin':
        return True
    return bool(
        str(os.environ.get('DISPLAY', '') or '').strip()
        or str(os.environ.get('WAYLAND_DISPLAY', '') or '').strip()
    )

def resolve_browser_headless_default() -> tuple[bool, str]:
    """Return (headless, reason) for this host.

    Order matters: an explicit operator setting always wins, then hosts that
    *cannot* show a window (containers or environments without display).
    When a display server is present, HEAD mode (headed: visible browser window)
    is the default across both interactive developer runs and packaged builds.
    """
    for env_name in _INTERNET_BROWSER_HEADLESS_ENV_VARS:
        configured = str(os.getenv(env_name, '') or '').strip().lower()
        if configured in _TRUTHY_ENV_VALUES:
            return True, f'{env_name}=headless'
        if configured in _FALSY_ENV_VALUES:
            return False, f'{env_name}=headed'

    if _running_in_container():
        return True, 'container has no display server'

    if not _host_has_a_display():
        return True, 'no DISPLAY/WAYLAND_DISPLAY on this host'

    return False, 'headed browser mode'

def _retrieved_at() -> str:
    """Return the host clock as an ISO timestamp, stamped onto every result.

    A model may ignore a prompt-injected clock. A timestamp in the tool data
    keeps the retrieval time attached to the evidence it is reading.
    """
    from datetime import datetime

    return datetime.now().isoformat(timespec='seconds')

def describe_exception(exc: BaseException) -> str:
    """Return a non-empty description of an exception.

    Several failures that matter here carry no message at all -
    ``str(NotImplementedError())`` is ``''`` - and an empty string travels all
    the way to the user as "the browser session failed: " with nothing after
    the colon. Fall back to the exception type so the report says something.
    """
    message = str(exc or "").strip()
    return message or type(exc).__name__

def is_playwright_available() -> bool:
    """Return whether browser-backed internet tooling can run."""
    from shared.native_webkit import browser_executable
    if browser_executable():
        return True
    return async_playwright is not None

def is_internet_access_enabled() -> bool:
    """Check global internet access switch.

    Prefers ServiceManager.config.internet_search_enabled; falls back to env var
    AUTOYOU_INTERNET_SEARCH_ENABLED ('1'/'0'), defaulting to True.
    """
    try:
        from service_manager import get_service_manager
        sm = get_service_manager()
        return bool(getattr(sm, 'config', None) and sm.config.internet_search_enabled)
    except Exception:
        pass
    try:
        val = os.environ.get('AUTOYOU_INTERNET_SEARCH_ENABLED')
        if val is not None:
            return val.strip() in ('1', 'true', 'True')
    except Exception:
        pass
    return True

class PlaywrightDriverManager:
    """Singleton Playwright browser manager with robust fallback mechanisms."""
    
    _instance = None
    _playwright = None
    _browser = None
    _browser_headless: Optional[bool] = None
    _active_contexts: int = 0
    _browser_pids = set()  # Track browser process IDs we create
    _owner_loop: Optional[asyncio.AbstractEventLoop] = None
    _owner_loop_tid: Optional[int] = None
    _browser_condition: Optional[asyncio.Condition] = None
    _browser_condition_loop: Optional[asyncio.AbstractEventLoop] = None
    _cleanup_started: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._browser_pids = set()  # Initialize PID tracking
            atexit.register(self.cleanup)
            # Initialize loop ownership markers
            self._owner_loop = None
            self._owner_loop_tid = None
            self._browser_condition = None
            self._browser_condition_loop = None
            self._active_contexts = 0
            self._cleanup_started = False
            self._browser_headless = None

    def _get_browser_condition(self) -> asyncio.Condition:
        """Return a loop-local condition guarding browser ownership."""
        loop = asyncio.get_running_loop()
        if self._browser_condition is None or self._browser_condition_loop is not loop:
            self._browser_condition = asyncio.Condition()
            self._browser_condition_loop = loop
        return self._browser_condition
    
    def _track_browser_processes(self, browser):
        """Track browser processes created by this manager."""
        try:
            import psutil
            # Get browser process info
            if hasattr(browser, '_impl_obj') and hasattr(browser._impl_obj, '_connection'):
                # Try to get process info from Playwright's internal connection
                logger.info("Tracking Playwright browser processes")
                
            # Find Chrome/Chromium processes that are likely ours
            for proc in psutil.process_iter(['pid', 'name', 'create_time', 'cmdline']):
                try:
                    if (proc.info['name'] and 
                        ('chrome' in proc.info['name'].lower() or 'chromium' in proc.info['name'].lower()) and
                        time.time() - proc.info['create_time'] < 10):  # Created within last 10 seconds
                        
                        # Check if it's a Playwright-launched process
                        cmdline = proc.info.get('cmdline', [])
                        if any('--remote-debugging-port' in arg for arg in cmdline if arg):
                            self._browser_pids.add(proc.info['pid'])
                            logger.info(f"Tracking Playwright browser process: {proc.info['pid']}")
                            
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                    
        except ImportError:
            logger.warning("psutil not available, cannot track browser processes precisely")
        except Exception as e:
            logger.warning(f"Error tracking browser processes: {e}")

    def _cleanup_tracked_processes(self, log_context: str) -> None:
        """Terminate any tracked Playwright browser processes left behind."""
        if not self._browser_pids:
            return

        try:
            import psutil
            cleaned_count = 0
            for pid in list(self._browser_pids):
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running():
                        proc.terminate()
                        cleaned_count += 1
                        logger.info(f"Terminated browser process: {pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                except Exception as e:
                    logger.warning(f"Error terminating process {pid}: {e}")

            if cleaned_count > 0:
                logger.info(f"Cleaned up {cleaned_count} browser processes ({log_context})")
        except ImportError:
            logger.warning(f"psutil not available for process cleanup ({log_context})")
        except Exception as e:
            logger.warning(f"Error during process cleanup ({log_context}): {e}")
        finally:
            self._browser_pids.clear()

    def get_browser_options(self, headless: Optional[bool] = None) -> Dict[str, Any]:
        """Get optimized browser launch options for Playwright."""
        if headless is None:
            headless = self._resolve_default_headless_setting()

        options = {
            'headless': bool(headless),
            'args': [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--disable-extensions',
                '--log-level=3',
                '--window-size=1280,720'
            ]
        }
        
        # Add Windows-specific options
        if os.name == 'nt':
            options['args'].extend([
                '--disable-gpu',
                '--disable-gpu-sandbox'
            ])
        
        return options

    @staticmethod
    def _resolve_default_headless_setting() -> bool:
        """Resolve the default headless setting for this host."""
        return resolve_browser_headless_default()[0]

    async def _ensure_playwright_started_locked(self) -> None:
        """Start Playwright under the browser ownership lock if needed."""
        if async_playwright is None:
            raise RuntimeError(_PLAYWRIGHT_UNAVAILABLE_MESSAGE)

        if self._playwright:
            return

        try:
            self._owner_loop = asyncio.get_running_loop()
            self._owner_loop_tid = threading.get_ident()
        except Exception:
            self._owner_loop = None
            self._owner_loop_tid = None

        self._playwright = await async_playwright().start()

    async def _launch_browser_locked(self, desired_headless: bool) -> Optional[AsyncBrowser]:
        """Launch a fresh browser instance while holding the ownership lock."""
        logger.info(
            "Creating new Playwright browser (async, headless=%s, default=%s)...",
            desired_headless,
            resolve_browser_headless_default()[1],
        )

        # Try Chromium first
        try:
            options = self.get_browser_options(desired_headless)
            browser = await self._playwright.chromium.launch(**options)
            self._track_browser_processes(browser)
            logger.info("Successfully created Chromium browser")
            return browser

        except Exception as e:
            logger.warning(f"Chromium launch failed: {e}")

        # Fallback to Firefox
        try:
            firefox_options = {
                'headless': desired_headless,
                'args': ['--width=1280', '--height=720']
            }
            browser = await self._playwright.firefox.launch(**firefox_options)
            self._track_browser_processes(browser)
            logger.info("Successfully created Firefox browser")
            return browser

        except Exception as e:
            logger.warning(f"Firefox launch failed: {e}")

        # Fallback to WebKit
        try:
            webkit_options = {'headless': desired_headless}
            browser = await self._playwright.webkit.launch(**webkit_options)
            self._track_browser_processes(browser)
            logger.info("Successfully created WebKit browser")
            return browser

        except Exception as e:
            logger.warning(f"WebKit launch failed: {e}")

        return None

    async def _close_browser_locked(self, reason: str) -> None:
        """Close the current shared browser while holding the ownership lock."""
        if self._browser:
            try:
                logger.info("Closing Playwright browser: %s", reason)
                await self._browser.close()
                logger.info("Browser closed successfully (async)")
            except Exception as e:
                logger.warning(f"Error closing browser (async): {e}")
            finally:
                self._browser = None
                self._browser_headless = None

        self._cleanup_tracked_processes("async")

    async def _stop_playwright_locked(self) -> None:
        """Stop the shared Playwright runtime while holding the ownership lock."""
        if not self._playwright:
            return

        try:
            await self._playwright.stop()
            logger.info("Playwright stopped successfully (async)")
        except Exception as e:
            logger.warning(f"Error stopping Playwright (async): {e}")
        finally:
            self._playwright = None
            self._owner_loop = None
            self._owner_loop_tid = None

    async def get_browser(self, headless: Optional[bool] = None) -> Optional[AsyncBrowser]:
        """Acquire the shared Playwright browser for one request-scoped use."""
        desired_headless = self._resolve_default_headless_setting() if headless is None else bool(headless)

        condition = self._get_browser_condition()
        async with condition:
            while True:
                if self._browser:
                    try:
                        self._browser.contexts
                    except Exception:
                        self._browser = None
                        self._browser_headless = None
                        self._active_contexts = 0

                if self._browser and self._browser_headless == desired_headless:
                    self._active_contexts += 1
                    return self._browser

                if self._browser and self._browser_headless != desired_headless:
                    if self._active_contexts > 0:
                        logger.info(
                            "Waiting for %s active Playwright context(s) to close before switching visibility: headless=%s -> %s",
                            self._active_contexts,
                            self._browser_headless,
                            desired_headless,
                        )
                        await condition.wait()
                        continue

                    logger.info(
                        "Recreating Playwright browser because visibility changed: headless=%s -> %s",
                        self._browser_headless,
                        desired_headless,
                    )
                    await self._close_browser_locked("visibility changed")

                try:
                    await self._ensure_playwright_started_locked()
                    browser = await self._launch_browser_locked(desired_headless)
                    if not browser:
                        raise Exception("Failed to create any Playwright browser")

                    self._browser = browser
                    self._browser_headless = desired_headless
                    self._active_contexts += 1
                    return self._browser
                except Exception as e:
                    logger.error(f"Failed to create any Playwright browser: {describe_exception(e)}")
                    await self._stop_playwright_locked()
                    raise

    async def release_browser(self) -> None:
        """Release one request-scoped browser lease and close idle resources."""
        condition = self._get_browser_condition()
        async with condition:
            if self._active_contexts > 0:
                self._active_contexts -= 1

            if self._active_contexts == 0:
                await self._close_browser_locked("last request-scoped context released")
                await self._stop_playwright_locked()

            condition.notify_all()

    def cleanup(self):
        """Cleanup entry used by atexit. Avoids cross-loop async calls.

        This will attempt to schedule an async cleanup on the owner event loop
        if it's still running. Otherwise, it will perform a best-effort
        process-level cleanup to ensure no browser processes are left behind.
        """
        try:
            if self._cleanup_started:
                return
            self._cleanup_started = True

            logger.info("Cleaning up Playwright resources (atexit)...")

            # Prefer closing on owner loop to avoid 'Event loop is closed' errors
            try:
                if self._owner_loop and not self._owner_loop.is_closed():
                    # If loop is running (possibly in another thread), schedule cleanup
                    if self._owner_loop.is_running():
                        fut = asyncio.run_coroutine_threadsafe(self.async_cleanup(), self._owner_loop)
                        try:
                            fut.result(timeout=10)
                            return
                        except Exception as e:
                            logger.warning(f"Owner-loop async cleanup failed: {e}")
            except Exception:
                # Owner loop not available or not suitable; fall back below
                pass

            # Fallback: do not call Playwright async APIs from a different/closed loop.
            # Just mark objects for GC and clean browser processes.
            self._browser = None
            self._browser_headless = None
            self._playwright = None
            self._active_contexts = 0

            # Clean up tracked processes (cross-platform)
            self._cleanup_tracked_processes("fallback")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    async def async_cleanup(self):
        """Async cleanup for Playwright resources using the current event loop.
        Prefer this when an event loop is available (e.g., in tests) to avoid
        Windows transport warnings caused by closing resources in a different loop.
        """
        try:
            logger.info("Async cleanup of Playwright resources...")

            condition = self._get_browser_condition()
            async with condition:
                self._active_contexts = 0
                await self._close_browser_locked("async cleanup requested")
                await self._stop_playwright_locked()
                condition.notify_all()

        except Exception as e:
            logger.error(f"Error during async cleanup: {e}")

def register_fastapi_cleanup(app: Any) -> None:
    """Register a FastAPI shutdown hook to cleanup Playwright resources.

    This should be called with the FastAPI app instances that live in the
    same event loop that created Playwright/browser resources. Doing so
    ensures graceful async cleanup without event-loop-closed errors.

    Args:
        app: A FastAPI application instance.
    """
    try:
        # Use add_event_handler to avoid the deprecated on_event decorator
        async def _shutdown_cleanup() -> None:
            try:
                await _driver_manager.async_cleanup()
            except Exception as e:
                logger.warning(f"Playwright cleanup on shutdown failed: {e}")

        app.router.add_event_handler("shutdown", _shutdown_cleanup)
    except Exception as e:
        logger.warning(f"Failed to register FastAPI cleanup hook: {e}")

_driver_manager = PlaywrightDriverManager()

def _internet_ssrf_guard_disabled() -> bool:
    """Operators can opt out of the private-IP guard for contained lab work
    (e.g. agent regression tests that need to hit http://127.0.0.1:<port>).
    Off by default; any truthy value in AUTOYOU_INTERNET_ALLOW_PRIVATE_IPS
    disables the guard.
    """
    raw = str(os.environ.get("AUTOYOU_INTERNET_ALLOW_PRIVATE_IPS", "")).strip().lower()
    return raw in _TRUTHY_ENV_VALUES

# Per-host verdicts for the SSRF route guard. A news homepage fires hundreds
# of subresource requests across a couple of dozen hosts, and the guard's
# safety check resolves DNS with a *blocking* socket call - unresolvable hosts
# (analytics beacons pointing at internal names) stall the longest. Run once
# per host and reuse the answer for a short window; the TTL stays small so a
# host that starts resolving somewhere private is re-checked promptly.
_SSRF_HOST_DECISION_TTL_SECONDS = 60.0
_SSRF_HOST_DECISION_CACHE_MAX = 512
_ssrf_host_decisions: Dict[tuple, tuple] = {}
# One in-flight resolution per host. A page opens dozens of parallel requests
# to the same CDN at once; without this they all miss the cold cache together
# and each occupies a worker thread on the same DNS lookup.
_ssrf_host_locks: Dict[tuple, asyncio.Lock] = {}
_ssrf_host_locks_loop: Optional[asyncio.AbstractEventLoop] = None

def _ssrf_decision_cache_key(url: str) -> Optional[tuple]:
    """Return the (scheme, host) the safety verdict actually depends on."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
    except Exception:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return (parts.scheme.lower(), parts.netloc.lower())

def _cached_ssrf_decision(key: Optional[tuple]) -> Optional[bool]:
    if key is None:
        return None
    entry = _ssrf_host_decisions.get(key)
    if not entry:
        return None
    decision, expires_at = entry
    if expires_at <= time.monotonic():
        _ssrf_host_decisions.pop(key, None)
        return None
    return bool(decision)

def _ssrf_host_locks_for_loop() -> Dict[tuple, asyncio.Lock]:
    """Return the lock table for the running loop, resetting it on a loop change.

    An asyncio.Lock binds to the loop that first awaits it and raises if reused
    from another, so the table cannot outlive its loop.
    """
    global _ssrf_host_locks, _ssrf_host_locks_loop
    loop = asyncio.get_running_loop()
    if _ssrf_host_locks_loop is not loop:
        _ssrf_host_locks = {}
        _ssrf_host_locks_loop = loop
    return _ssrf_host_locks

@asynccontextmanager
async def _ssrf_host_lock(key: Optional[tuple]):
    """Serialize resolution of one host; a no-op when there is nothing to key on."""
    if key is None:
        yield
        return
    locks = _ssrf_host_locks_for_loop()
    if len(locks) >= _SSRF_HOST_DECISION_CACHE_MAX:
        locks.clear()
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    async with lock:
        yield

def _store_ssrf_decision(key: Optional[tuple], decision: bool) -> None:
    if key is None:
        return
    if len(_ssrf_host_decisions) >= _SSRF_HOST_DECISION_CACHE_MAX:
        _ssrf_host_decisions.clear()
    _ssrf_host_decisions[key] = (bool(decision), time.monotonic() + _SSRF_HOST_DECISION_TTL_SECONDS)

async def _install_ssrf_route_guard(context) -> None:
    """Install a catch-all route on the Playwright browser context that
    aborts any outgoing request whose resolved host is loopback/private/
    link-local/reserved.

    Rationale: the internet agent is a general-purpose web tool; it should
    not be weaponizable as an SSRF primitive against the operator's own
    LAN, cloud-metadata endpoints, or loopback services just because a
    prompt (or a page the agent visits) said so.
    """
    if _internet_ssrf_guard_disabled():
        logger.warning(
            "Internet agent SSRF guard is DISABLED via "
            "AUTOYOU_INTERNET_ALLOW_PRIVATE_IPS; browser can reach private IPs."
        )
        return

    try:
        from shared.url_safety import is_safe_http_url
    except Exception as exc:
        logger.warning("Could not import url_safety for SSRF guard: %s", exc)
        return

    async def _guard(route, request):
        target_url = request.url or ""
        cache_key = _ssrf_decision_cache_key(target_url)
        safe = _cached_ssrf_decision(cache_key)

        if safe is None:
            async with _ssrf_host_lock(cache_key):
                # A queued caller may have resolved this host while waiting.
                safe = _cached_ssrf_decision(cache_key)
                if safe is None:
                    try:
                        # url_safety rejects anything whose resolved IP is
                        # loopback / private / link-local / reserved /
                        # multicast / unspecified, as well as non-http(s)
                        # schemes. It resolves DNS with a blocking socket call,
                        # so keep it off the event loop the server shares.
                        safe = await asyncio.to_thread(is_safe_http_url, target_url)
                    except Exception:
                        safe = False
                    _store_ssrf_decision(cache_key, safe)

        if safe:
            try:
                await route.continue_()
            except Exception:
                pass
            return

        logger.warning(
            "Internet agent blocked request to disallowed URL: %s",
            target_url[:300],
        )
        try:
            await route.abort("blockedbyclient")
        except Exception:
            # If abort fails we still do not want the request to proceed;
            # fulfill with an explicit 403 so the page sees a clear failure.
            try:
                await route.fulfill(status=403, body="blocked by autoyou ssrf guard")
            except Exception:
                pass

    try:
        await context.route("**/*", _guard)
    except Exception as exc:
        logger.warning("Failed to install Playwright SSRF route guard: %s", exc)

@asynccontextmanager
async def get_playwright_browser(headless: Optional[bool] = None):
    """Async context manager for Playwright browser with automatic cleanup."""
    from shared.native_webkit import browser_executable, webkit_page
    native_browser = browser_executable()
    if native_browser:
        async with webkit_page(native_browser,
                               headless=resolve_browser_headless_default()[0] if headless is None else headless,
                               allow_private=_internet_ssrf_guard_disabled()) as page:
            yield page
        return
    browser = None
    context = None
    page = None
    browser_acquired = False
    # from __debug_provenance_l__ import because

    try:
        logger.info("Getting Playwright browser...")
        browser = await _driver_manager.get_browser(headless=headless)
        browser_acquired = browser is not None

        if not browser:
            raise Exception("Failed to create Playwright browser")

        # Create a new browser context for isolation
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        # Install the SSRF guard on this context before creating any page so
        # the very first navigation is covered.
        await _install_ssrf_route_guard(context)

        # Create a new page
        page = await context.new_page()

        # Set timeouts
        page.set_default_timeout(30000)  # 30 seconds
        page.set_default_navigation_timeout(30000)  # 30 seconds

        logger.info("Playwright browser context and page created successfully")
        yield page
        
    except Exception as e:
        logger.error(f"Error in Playwright browser context: {describe_exception(e)}")
        raise
        
    finally:
        # Clean up in reverse order
        try:
            if page:
                await page.close()
                logger.info("Playwright page closed")
        except Exception as e:
            logger.warning(f"Error closing Playwright page: {e}")
            
        try:
            if context:
                await context.close()
                logger.info("Playwright context closed")
        except Exception as e:
            logger.warning(f"Error closing Playwright context: {e}")

        try:
            if browser_acquired:
                await _driver_manager.release_browser()
        except Exception as e:
            logger.warning(f"Error releasing Playwright browser: {e}")

def _resolve_headless_override(
    headless: Optional[bool] = None,
    headed: Optional[bool] = None,
) -> Optional[bool]:
    """Normalize explicit headed/headless overrides into a headless bool."""
    if headed is not None:
        return not bool(headed)
    if headless is not None:
        return bool(headless)
    return None

# Tags whose text is markup plumbing, not page content. Content-heavy sites inline
# large JSON-LD/analytics blobs, and BeautifulSoup's get_text() happily returns
# all of it - which used to consume the whole 5000-char budget before a single
# headline appeared, leaving the model to summarize minified JavaScript.
_NON_CONTENT_TAGS = ('script', 'style', 'noscript', 'template', 'svg')
_MAX_SCRAPED_TEXT_CHARS = 5000
_MIN_READABLE_TEXT_CHARS = 200
_MAX_SCRAPED_LINKS = 50
_MAX_SCRAPED_MEDIA = 10
_MAX_LINK_TEXT_CHARS = 120
_NON_NAVIGABLE_URL_PREFIXES = ('javascript:', 'mailto:', 'tel:', 'data:', 'blob:', '#')

def _absolutize_scraped_url(base_url: str, candidate: Any) -> str:
    """Resolve any href/src form against the page URL, or return '' to skip it.

    Handles root-relative ('/x'), protocol-relative ('//cdn/x') and plain
    relative ('x/y.html') references; only the first form used to be resolved,
    so most of a scraped page's links were handed back unusable.
    """
    href = str(candidate or '').strip()
    if not href or href.lower().startswith(_NON_NAVIGABLE_URL_PREFIXES):
        return ''
    try:
        resolved = urljoin(base_url, href)
    except Exception:
        return ''
    return resolved if resolved.lower().startswith(('http://', 'https://')) else ''

def _strip_non_content_tags(soup: Any) -> None:
    """Drop script/style/etc. in place so text and links reflect real content."""
    for tag in soup(list(_NON_CONTENT_TAGS)):
        try:
            tag.decompose()
        except Exception:
            continue

# Whole-document text on a dynamic site starts with chrome - cookie notices, ad
# feedback surveys, nav menus - and the character budget runs out before the
# first headline. The main-content region holds what the page is actually
# about, so prefer it whenever the page marks one up.
_MAIN_CONTENT_SELECTORS = ('main', 'article', '[role="main"]', '#main-content')
_MIN_MAIN_CONTENT_CHARS = 500
_MAX_SCRAPED_HEADLINES = 20
_MAX_HEADLINE_TEXT_CHARS = 240
_NON_ARTICLE_HEADING_PATTERN = re.compile(
    r"\b(?:drm system|more top stories|sign in|sign up|streaming now|watch live)\b",
    re.IGNORECASE,
)

def _extract_readable_text(soup: Any) -> str:
    """Return the page's main-region text, or all of it when there is no region."""
    full_text = soup.get_text(separator=' ', strip=True)

    best_region_text = ''
    for selector in _MAIN_CONTENT_SELECTORS:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        for element in elements:
            candidate = element.get_text(separator=' ', strip=True)
            if len(candidate) > len(best_region_text):
                best_region_text = candidate

    if len(best_region_text) >= _MIN_MAIN_CONTENT_CHARS:
        return best_region_text
    return full_text


def _extract_page_headlines(soup: Any, base_url: str) -> List[Dict[str, str]]:
    """Extract heading text and its closest article link from readable markup."""
    regions: List[Any] = []
    for selector in _MAIN_CONTENT_SELECTORS:
        try:
            regions.extend(soup.select(selector))
        except Exception:
            continue
    roots = regions or [soup]
    headlines: List[Dict[str, str]] = []
    seen_titles = set()
    for root in roots:
        try:
            headings = root.find_all(("h1", "h2", "h3"))
        except Exception:
            continue
        for heading in headings:
            title = " ".join((heading.get_text(" ", strip=True) or "").split()).strip()
            normalized_title = title.lower()
            if not (12 <= len(title) <= _MAX_HEADLINE_TEXT_CHARS):
                continue
            if normalized_title in seen_titles:
                continue
            anchor = heading.find("a", href=True) or heading.find_parent("a", href=True)
            if anchor is None or _NON_ARTICLE_HEADING_PATTERN.search(title):
                continue
            href = _absolutize_scraped_url(base_url, anchor.get("href"))
            if not href:
                continue
            seen_titles.add(normalized_title)
            headlines.append({"title": title, "url": href})
            if len(headlines) >= _MAX_SCRAPED_HEADLINES:
                return headlines
    return headlines

def _page_metrics_are_stable(previous: Dict[str, int], current: Dict[str, int]) -> bool:
    return (
        abs(current['text_len'] - previous['text_len']) < 64
        and abs(current['node_count'] - previous['node_count']) < 8
        and abs(current['scroll_height'] - previous['scroll_height']) < 32
    )

# Wall-clock ceiling for getting a page into a readable state. Enforced as a
# deadline across every await below, not just checked between them.
_PAGE_READY_BUDGET_MS = 12000


def _normalize_navigation_actions(actions: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalize provider-shaped navigation actions into a list of mappings."""
    if actions is None:
        return []

    if isinstance(actions, str):
        encoded = actions.strip()
        if not encoded:
            return []
        try:
            actions = json.loads(encoded)
        except (TypeError, ValueError):
            return None

    if isinstance(actions, dict):
        actions = [actions]
    if not isinstance(actions, (list, tuple)):
        return None

    normalized: List[Dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            return None
        normalized.append(dict(action))
    return normalized

async def _sample_page_metrics(page: Any) -> Dict[str, int]:
    return await page.evaluate(
        r"""
        () => {
            const body = document.body;
            const text = (body?.innerText || '').replace(/\s+/g, ' ').trim();
            const doc = document.documentElement;
            return {
                text_len: text.length,
                node_count: document.getElementsByTagName('*').length,
                scroll_height: Math.max(
                    doc?.scrollHeight || 0,
                    body?.scrollHeight || 0,
                ),
            };
        }
        """
    )

async def _goto_page_ready(page: Any, url: str) -> None:
    """Navigate until the rendered page stops materially changing within a budget.

    The budget is enforced as a real deadline. It used to gate only the top of
    the sampling loop, so any single slow await ran past it unchecked when a
    page kept mutating its DOM.
    """
    start = time.monotonic()
    total_timeout_ms = _PAGE_READY_BUDGET_MS
    sample_interval_ms = 300
    stable_samples_needed = 2

    def _remaining_ms() -> int:
        return total_timeout_ms - int((time.monotonic() - start) * 1000)

    await page.goto(url, wait_until="domcontentloaded")

    try:
        await page.wait_for_load_state("load", timeout=max(0, min(3000, _remaining_ms())))
    except Exception:
        pass

    try:
        await page.wait_for_function(
            r"""
            () => {
                const body = document.body;
                if (!body) {
                    return false;
                }
                const rect = body.getBoundingClientRect();
                const doc = document.documentElement;
                return document.readyState !== 'loading'
                    && rect.width > 0
                    && rect.height > 0
                    && Math.max(doc?.scrollHeight || 0, body.scrollHeight || 0) > 0;
            }
            """,
            timeout=max(0, min(3000, _remaining_ms())),
        )
    except Exception:
        pass

    stable_samples = 0
    last_metrics: Optional[Dict[str, int]] = None

    while True:
        remaining_ms = _remaining_ms()
        if remaining_ms <= 0:
            break

        try:
            # Bounded: a busy main thread can otherwise park this evaluate for
            # longer than the whole readiness budget.
            current_metrics = await asyncio.wait_for(
                _sample_page_metrics(page),
                timeout=max(0.05, remaining_ms / 1000),
            )
        except Exception:
            break

        if last_metrics is not None and _page_metrics_are_stable(last_metrics, current_metrics):
            stable_samples += 1
            if stable_samples >= stable_samples_needed:
                break
        else:
            stable_samples = 0

        last_metrics = current_metrics
        if remaining_ms <= sample_interval_ms:
            break
        try:
            await asyncio.wait_for(
                page.wait_for_timeout(min(sample_interval_ms, remaining_ms)),
                timeout=max(0.05, _remaining_ms() / 1000),
            )
        except Exception:
            break

class InternetTool:
    """Internet tool for web browsing and searching."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    @staticmethod
    def _build_search_success(
        query: str,
        results: List[Dict[str, Any]],
        provider: str,
    ) -> Dict[str, Any]:
        return {
            'status': 'success',
            'results': results,
            'results_count': len(results),
            'query': query,
            'provider': provider,
            'retrieved_at': _retrieved_at(),
        }

    @staticmethod
    def _build_search_error(query: str, error: str, provider: str) -> Dict[str, Any]:
        return {
            'status': 'error',
            'error': error,
            'results': [],
            'results_count': 0,
            'query': query,
            'provider': provider,
        }

    @staticmethod
    def _looks_like_duckduckgo_bot_challenge(document_text: str) -> bool:
        lowered = str(document_text or '').lower()
        return any(marker in lowered for marker in (
            'anomaly-modal',
            'anomaly.js?',
            'bots use duckduckgo too',
            'confirm this search was made by a human',
        ))

    @staticmethod
    def _parse_bing_rss_results(payload: str, max_results: int) -> List[Dict[str, Any]]:
        root = ET.fromstring(payload)
        items = root.findall('./channel/item')
        results: List[Dict[str, Any]] = []

        for item in items[:max_results]:
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            description_raw = (item.findtext('description') or '').strip()
            description = BeautifulSoup(description_raw, 'html.parser').get_text(' ', strip=True)
            if not description:
                description = 'No description available'
            if title and link:
                results.append({
                    'title': title,
                    'url': link,
                    'snippet': description,
                    'description': description,
                })

        return results

    @staticmethod
    def _site_filter_domain(query: str) -> str:
        """Return the domain a `site:` operator restricts the query to."""
        match = _SITE_OPERATOR_PATTERN.search(str(query or ''))
        if not match:
            return ''
        raw_target = match.group(1).strip().strip('.,;/')
        parsed = urlsplit(f'//{raw_target}')
        host = (parsed.hostname or raw_target.split('/', 1)[0]).strip().lower()
        return host.removeprefix('www.')

    @staticmethod
    def _normalize_bing_site_query(query: str) -> str:
        """Normalize path-bearing site operators for Bing RSS.

        Bing RSS reliably honors a host restriction but may treat
        ``site:bbc.com/news`` as a literal host and return no results. Keep
        the host restriction and drop the path from the provider query; the
        host filter below still prevents off-domain results from leaking out.
        """
        text = str(query or '')
        match = _SITE_OPERATOR_PATTERN.search(text)
        if not match:
            return text

        raw_target = match.group(1).strip().strip('.,;/')
        parsed = urlsplit(f'//{raw_target}')
        host = (parsed.hostname or raw_target.split('/', 1)[0]).strip().lower()
        if not host:
            return text

        replacement = f'site:{host.removeprefix("www.")}'
        return f'{text[:match.start()]}{replacement}{text[match.end():]}'

    @staticmethod
    def _result_is_on_domain(result: Dict[str, Any], domain: str) -> bool:
        host = urlsplit(str(result.get('url') or '')).netloc.lower()
        host = host.split('@')[-1].split(':')[0].removeprefix('www.')
        return bool(host) and (host == domain or host.endswith(f'.{domain}'))

    def internet_search_with_bing_rss(self, query: str, max_results: int = 10) -> Dict[str, Any]:
        """Perform an Internet search via Bing's RSS endpoint.

        This is the most reliable lightweight provider for the packaged Windows
        build because it avoids the DuckDuckGo Lite bot challenge that frequently
        appears for non-interactive requests.
        """
        try:
            logger.info(f"Performing Internet search with Bing RSS for: {query}")
            provider_query = self._normalize_bing_site_query(query)
            url = f"https://www.bing.com/search?format=rss&q={quote_plus(provider_query)}"
            response = self.session.get(
                url,
                headers={
                    'Accept': 'application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5',
                },
                timeout=10,
            )
            response.raise_for_status()

            # This endpoint can silently ignore a `site:` operator. Read a
            # deeper pool so on-domain hits further down still survive the
            # filter below.
            domain = self._site_filter_domain(query)
            results = self._parse_bing_rss_results(
                response.text,
                max(max_results, 20) if domain else max_results,
            )
            if not results:
                logger.warning('Bing RSS returned no parsable results')
                return self._build_search_error(query, 'No search results extracted from Bing RSS', 'bing_rss')

            if domain:
                on_domain = [item for item in results if self._result_is_on_domain(item, domain)]
                if not on_domain:
                    # Handing back off-domain results would make the caller
                    # mistake one source for another. Fail so the browser-backed
                    # provider, which honours site:, can run.
                    logger.warning("Bing RSS ignored the site:%s restriction; no on-domain results", domain)
                    return self._build_search_error(
                        query,
                        f'Bing RSS returned no results from {domain} for a site:-restricted query',
                        'bing_rss',
                    )
                results = on_domain

            results = results[:max_results]
            logger.info(f"Found {len(results)} search results using Bing RSS")
            return self._build_search_success(query, results, 'bing_rss')
        except Exception as e:
            logger.warning(f"Bing RSS Internet search failed: {describe_exception(e)}")
            return self._build_search_error(query, describe_exception(e), 'bing_rss')
    
    def internet_search_with_requests(self, query: str, max_results: int = 10) -> Dict[str, Any]:
        """Perform Internet search using requests only (no fallback)."""
        if not is_internet_access_enabled():
            logger.info("Internet search blocked: internet access disabled")
            return {
                'status': 'disabled',
                'enabled': False,
                'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
                'transfer_to': 'autoyou_agent',
                'results': [],
                'results_count': 0,
                'query': query,
            }
        try:
            from bs4 import BeautifulSoup
            import urllib.parse
            
            logger.info(f"Performing Internet search with DuckDuckGo Lite for: {query}")
            
            # Prepare search URL - use DuckDuckGo Lite endpoint for static HTML
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://lite.duckduckgo.com/lite/?q={encoded_query}"
            
            # Headers to mimic a real browser
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            # Make request
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            if self._looks_like_duckduckgo_bot_challenge(response.text):
                logger.warning(
                    'DuckDuckGo Lite returned an anti-bot challenge for query: %s; falling back to Bing RSS',
                    query,
                )
                bing_results = self.internet_search_with_bing_rss(query, max_results)
                if bing_results.get('results_count', 0) > 0:
                    return bing_results
                return self._build_search_error(
                    query,
                    'DuckDuckGo Lite returned an anti-bot challenge and Bing RSS fallback did not return results',
                    'duckduckgo_lite',
                )
            
            # Parse HTML
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract search results from DuckDuckGo Lite (static HTML)
            results = []
            
            # DuckDuckGo Lite selectors (stable HTML)
            anchor_results = soup.select('a.result-link')
            for a in anchor_results[:max_results]:
                try:
                    title = (a.get_text() or '').strip()
                    href = a.get('href') or ''
                    # Resolve DuckDuckGo redirect URLs
                    if href.startswith('//duckduckgo.com/l/?uddg=') or href.startswith('/l/?uddg='):
                        import urllib.parse
                        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                        if 'uddg' in parsed:
                            href = urllib.parse.unquote(parsed['uddg'][0])
                    if title and href.startswith('http'):
                        # Try to find a nearby snippet in the same table row
                        snippet = "No snippet"
                        parent = a.find_parent('td')
                        if parent:
                            sn = parent.find_next_sibling('td')
                            if sn:
                                s = sn.get_text().strip()
                                if s:
                                    snippet = s
                        results.append({
                            'title': title,
                            'url': href,
                            'snippet': snippet,
                            'description': snippet
                        })
                except Exception:
                    continue
            
            # If no results were extracted, fallback to Bing RSS
            if len(results) == 0:
                logger.warning("DuckDuckGo Lite returned no parsable results, falling back to Bing RSS")
                bing_results = self.internet_search_with_bing_rss(query, max_results)
                if bing_results.get('results_count', 0) > 0:
                    return bing_results
                return self._build_search_error(
                    query,
                    'No search results extracted from DuckDuckGo Lite or Bing RSS',
                    'duckduckgo_lite',
                )

            logger.info(f"Found {len(results)} search results using requests")
            return self._build_search_success(query, results, 'duckduckgo_lite')
            
        except Exception as e:
            logger.error(f"Requests-based Internet search failed: {describe_exception(e)}")
            logger.info("Falling back to Bing RSS due to exception")
            bing_results = self.internet_search_with_bing_rss(query, max_results)
            if bing_results.get('results_count', 0) > 0:
                return bing_results
            return self._build_search_error(query, describe_exception(e), 'duckduckgo_lite')
    
    async def internet_search(self, query: str, max_results: int = 10) -> Dict[str, Any]:
        """
        Search Internet for information and return results (async).
        
        Args:
            query: The search query
            max_results: Maximum number of results to return (default: 10)
            
        Returns:
            Dictionary with search results, URLs, titles, and descriptions
        """
        query = _normalize_search_query_input(query)
        invalid_query_reason = _invalid_search_query_reason(query)
        if invalid_query_reason:
            logger.warning("Internet search rejected an invalid query: %s", invalid_query_reason)
            return self._build_search_error(query[:_SEARCH_QUERY_MAX_LENGTH], invalid_query_reason, 'query_validation')

        if not is_internet_access_enabled():
            logger.info("Internet search (async) blocked: internet access disabled")
            return {
                'status': 'disabled',
                'enabled': False,
                'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
                'transfer_to': 'autoyou_agent',
                'results': [],
                'results_count': 0,
                'query': query,
            }
        request_results = await asyncio.to_thread(self.internet_search_with_requests, query, max_results)
        if request_results.get('results_count', 0) > 0:
            return request_results

        # A search-engine challenge is a provider response, not a request to
        # evade the provider's controls with a second browser fingerprint.
        # The requests path already tried the configured Bing RSS fallback;
        # surface the result when both providers declined the query.
        request_error = str(request_results.get('error') or '').lower()
        if 'anti-bot challenge' in request_error:
            return request_results

        try:
            logger.info(f"Performing Internet search for: {query}")
            
            async with get_playwright_browser() as page:
                # Prefer DuckDuckGo Lite endpoint for stable markup
                ddg_url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
                await page.goto(ddg_url, wait_until="domcontentloaded")
                # Early static HTML parse to avoid brittle selector waits/timeouts
                try:
                    await page.wait_for_timeout(1000)
                    html = await page.content()
                    if self._looks_like_duckduckgo_bot_challenge(html):
                        logger.warning('DuckDuckGo Lite returned an anti-bot challenge in Playwright for query: %s', query)
                        return request_results
                    soup = BeautifulSoup(html, 'html.parser')
                    anchors = soup.select('a.result-link')
                    if not anchors:
                        # Fallback: any link with DuckDuckGo redirect 'uddg' in query
                        anchors = [a for a in soup.select('a[href]') if a.get('href') and ('/l/?uddg=' in a.get('href') or 'uddg=' in a.get('href'))]
                    early_results: List[Dict[str, Any]] = []
                    for a in anchors[:max_results]:
                        try:
                            title = (a.get_text() or '').strip()
                            href = a.get('href') or ''
                            # Resolve DuckDuckGo redirect URLs
                            if href.startswith('//duckduckgo.com/l/?uddg=') or href.startswith('/l/?uddg=') or 'uddg=' in href:
                                from urllib.parse import parse_qs, urlparse, unquote
                                parsed = parse_qs(urlparse(href).query)
                                if 'uddg' in parsed:
                                    href = unquote(parsed['uddg'][0])
                            if title and href.startswith('http'):
                                # Try to find a nearby snippet (td sibling)
                                snippet = "No description available"
                                parent_td = a.find_parent('td')
                                if parent_td:
                                    sib_td = parent_td.find_next_sibling('td')
                                    if sib_td:
                                        s = (sib_td.get_text() or '').strip()
                                        if s:
                                            snippet = s
                                early_results.append({
                                    "title": title,
                                    "url": href,
                                    "description": snippet,
                                    "rank": len(early_results) + 1
                                })
                        except Exception:
                            continue
                    if early_results:
                                return {
                                    "query": str(query),
                                    "results_count": len(early_results),
                                    "results": early_results,
                                    "status": "success",
                                    "provider": "duckduckgo_playwright",
                                }
                except Exception:
                    pass
                
                # Handle cookie consent if present
                try:
                    accept_buttons = [
                        'button:has-text("Accept all")',
                        'button:has-text("I agree")', 
                        'button:has-text("Accept")',
                        'button[id*="accept"]',
                        'button[aria-label*="Accept"]'
                    ]
                    for selector in accept_buttons:
                        accept_button = page.locator(selector)
                        if await accept_button.count() > 0:
                            await accept_button.first.click()
                            await page.wait_for_timeout(1000)
                            break
                except Exception:
                    pass  # Cookie consent not present or already handled
                
                # Avoid long selector waits; be conservative
                await page.wait_for_timeout(1000)
                
                # Extract search results
                results = []
                
                # Try different result container selectors for DuckDuckGo
                result_containers = [
                    '[data-testid="result"]',  # DuckDuckGo main result container
                    '.result',                 # DuckDuckGo result class
                    '.result__body',           # DuckDuckGo result body
                    'article[data-testid="result"]',  # Alternative DuckDuckGo selector
                    '.web-result'              # Generic web result
                ]
                
                search_results = None
                for container_selector in result_containers:
                    search_results = page.locator(container_selector)
                    if await search_results.count() > 0:
                        logger.info(f"Found {await search_results.count()} results using selector: {container_selector}")
                        break

                if search_results and await search_results.count() > 0:
                    count = min(await search_results.count(), max_results)
                    for i in range(count):
                        try:
                            result = search_results.nth(i)
                            
                            # Extract title - DuckDuckGo selectors
                            title = "No title"
                            title_selectors = [
                                'h3 a[data-testid="result-title-a"]',  # DuckDuckGo title link
                                'h3',                                   # Generic h3 title
                                '.result__title a',                    # DuckDuckGo result title
                                'a[data-testid="result-title-a"]',     # DuckDuckGo title link without h3
                                '.result__a'                           # Alternative DuckDuckGo title
                            ]
                            for title_sel in title_selectors:
                                title_element = result.locator(title_sel).first
                                if await title_element.count() > 0:
                                    title_text = await title_element.text_content()
                                    if title_text and title_text.strip():
                                        title = title_text.strip()
                                        break
                            
                            # Extract URL - DuckDuckGo selectors
                            url = "No URL"
                            link_selectors = [
                                'a[data-testid="result-title-a"]',     # DuckDuckGo title link
                                '.result__title a',                    # DuckDuckGo result title link
                                'h3 a',                                # Generic h3 link
                                'a[href]',                             # Any link with href
                                '.result__a'                           # Alternative DuckDuckGo link
                            ]
                            for link_sel in link_selectors:
                                link_element = result.locator(link_sel).first
                                if await link_element.count() > 0:
                                    href = await link_element.get_attribute("href")
                                    if href and href.startswith('http'):
                                        url = href
                                        break
                            
                            # Extract description - DuckDuckGo selectors
                            description = "No description available"
                            desc_selectors = [
                                '.result__snippet',                    # DuckDuckGo snippet
                                '[data-testid="result-snippet"]',     # DuckDuckGo snippet with test id
                                '.result__body',                      # DuckDuckGo result body
                                '.snippet',                           # Generic snippet
                                'p',                                  # Generic paragraph
                                '.description'                        # Generic description
                            ]
                            for desc_sel in desc_selectors:
                                desc_elements = result.locator(desc_sel)
                                for j in range(await desc_elements.count()):
                                    text = await desc_elements.nth(j).text_content()
                                    if text and len(text.strip()) > 20:
                                        description = text.strip()
                                        break
                                if description != "No description available":
                                    break
                            
                            if title != "No title" and url != "No URL":
                                results.append({
                                    "title": title,
                                    "url": url,
                                    "description": description,
                                    "rank": i + 1
                                })
                            
                        except Exception as e:
                            logger.warning(f"Error extracting result {i}: {e}")
                            continue

                # Prefer direct anchors when containers not found (DuckDuckGo Lite)
                if len(results) == 0:
                    anchor_selectors = [
                        'a.result-link',
                        'a.result__a',
                        'h2 a[href]',
                        'h3 a[href]',
                        'a[href][data-testid="result-title-a"]'
                    ]
                    anchors = None
                    for a_sel in anchor_selectors:
                        anchors = page.locator(a_sel)
                        if await anchors.count() > 0:
                            logger.info(f"Found {await anchors.count()} anchors using selector: {a_sel}")
                            break
                    if anchors and await anchors.count() > 0:
                        count = min(await anchors.count(), max_results)
                        for i in range(count):
                            try:
                                a = anchors.nth(i)
                                title = await a.text_content() or "No title"
                                href = await a.get_attribute("href") or "No URL"
                                description = "No description available"
                                # Try to get a nearby snippet (Lite layout: title TD, snippet in sibling TD)
                                try:
                                    parent_td = a.locator('xpath=..')
                                    sibling_td = parent_td.locator('xpath=following-sibling::td[1]')
                                    if await sibling_td.count() > 0:
                                        s = await sibling_td.first.text_content()
                                        if s and s.strip():
                                            description = s.strip()
                                except Exception:
                                    pass
                                if title.strip() and href.startswith('http'):
                                    results.append({
                                        "title": title.strip(),
                                        "url": href,
                                        "description": description,
                                        "rank": i + 1
                                    })
                            except Exception as e:
                                logger.warning(f"Error extracting anchor result {i}: {e}")
                                continue
                
                logger.info(f"Successfully extracted {len(results)} search results")
                
                # If no results were extracted, surface as error instead of silently succeeding
                if len(results) == 0:
                    return {
                        "query": str(query),
                        "results_count": 0,
                        "results": [],
                        "status": "error",
                        "error": "No search results extracted from DuckDuckGo Lite with Playwright",
                        "provider": "duckduckgo_playwright",
                    }
                
                # Return properly serializable dictionary
                return {
                    "query": str(query),
                    "results_count": len(results),
                    "results": results,
                    "status": "success",
                    "provider": "duckduckgo_playwright",
                }
                
        except Exception as e:
            logger.warning(f"Playwright Internet search failed: {describe_exception(e)}")
            if request_results.get('results_count', 0) == 0:
                fallback_error = dict(request_results)
                fallback_error.setdefault('playwright_error', describe_exception(e))
                return fallback_error
            return {
                "query": str(query),
                "results_count": 0,
                "results": [],
                "status": "error",
                "error": describe_exception(e),
                "provider": "duckduckgo_playwright",
            }
    
    async def scrape_website(
        self,
        url: str,
        extract_media: bool = False,
        headless: Optional[bool] = None,
        headed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Scrape content from a website.

        Args:
            url: The URL to scrape
            extract_media: Whether to also return image/video/audio URLs. Off by
                default: image `alt` text reads like prose, and a model asked to
                summarize a news homepage will summarize the picture captions
                instead of the headlines sitting in `text_content`. Turn it on
                only when the media itself is the point.
            headless: Optional override for headless browser mode
            headed: Optional alias to request a visible browser window
            
        Returns:
            Dictionary with scraped content
        """
        if not is_internet_access_enabled():
            logger.info("Scrape (method) blocked: internet access disabled")
            return {
                'status': 'disabled',
                'enabled': False,
                'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
                'transfer_to': 'autoyou_agent',
                'url': url,
                'title': '',
                'text_content': '',
                'links': [],
                'media': [],
                'metadata': {}
            }
        try:
            logger.info(f"Scraping website: {url}")
            requested_headless = _resolve_headless_override(headless, headed)
            
            async with get_playwright_browser(requested_headless) as page:
                await _goto_page_ready(page, url)
                
                # Get page source and parse with BeautifulSoup
                page_source = await page.content()
                soup = BeautifulSoup(page_source, 'html.parser')

                # Page title (read before stripping, and tolerant of nested markup
                # inside <title>, where .string is None)
                title = soup.title.get_text(strip=True) if soup.title else ""
                title = title or "No title"

                _strip_non_content_tags(soup)

                # Extract text content
                text_content = _extract_readable_text(soup)
                headlines = _extract_page_headlines(soup, url)

                # Extract links, de-duplicated so a nav bar repeated in header,
                # footer and mobile menu does not fill the whole budget.
                links: List[Dict[str, Any]] = []
                seen_link_urls = set()
                for link in soup.find_all('a', href=True):
                    href = _absolutize_scraped_url(url, link.get('href'))
                    text = ' '.join((link.get_text(strip=True) or '').split())
                    if not href or not text or href in seen_link_urls:
                        continue
                    seen_link_urls.add(href)
                    links.append({"url": href, "text": text[:_MAX_LINK_TEXT_CHARS]})
                    if len(links) >= _MAX_SCRAPED_LINKS:
                        break

                # Extract media if requested
                media: List[Dict[str, Any]] = []
                seen_media_urls = set()

                def _add_media(item: Dict[str, Any]) -> bool:
                    """Record one media item; return False once the cap is reached."""
                    if len(media) >= _MAX_SCRAPED_MEDIA:
                        return False
                    media_url = item.get("url") or ""
                    if not media_url or media_url in seen_media_urls:
                        return True
                    seen_media_urls.add(media_url)
                    media.append(item)
                    return True

                if extract_media:
                    # Images
                    for img in soup.find_all('img', src=True):
                        if not _add_media({
                            "type": "image",
                            "url": _absolutize_scraped_url(url, img.get('src')),
                            "alt": img.get('alt', ''),
                            "width": img.get('width', ''),
                            "height": img.get('height', '')
                        }):
                            break

                    # Videos
                    for video in soup.find_all('video', src=True):
                        if not _add_media({
                            "type": "video",
                            "url": _absolutize_scraped_url(url, video.get('src')),
                            "controls": video.get('controls', ''),
                            "autoplay": video.get('autoplay', '')
                        }):
                            break

                    # Audio
                    for audio in soup.find_all('audio', src=True):
                        if not _add_media({
                            "type": "audio",
                            "url": _absolutize_scraped_url(url, audio.get('src')),
                            "controls": audio.get('controls', '')
                        }):
                            break

                # Extract metadata
                metadata = {}

                # Meta description
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                description = meta_desc.get('content', '') if meta_desc else ''
                
                # Meta keywords
                meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
                keywords = meta_keywords.get('content', '') if meta_keywords else ''
                
                metadata = {
                    'title': title,
                    'description': description,
                    'keywords': keywords,
                    'url': url
                }
                
                trimmed_text = text_content[:_MAX_SCRAPED_TEXT_CHARS]
                result: Dict[str, Any] = {
                    "url": str(url),
                    "retrieved_at": _retrieved_at(),
                    "title": title,
                    "text_content": trimmed_text,
                    "text_truncated": len(text_content) > len(trimmed_text),
                    "headlines": headlines,
                    "links": links,
                    "media": media if extract_media else [],
                    "metadata": metadata,
                    "browser_headless": requested_headless if requested_headless is not None else PlaywrightDriverManager._resolve_default_headless_setting(),
                    "status": "success"
                }

                # A page that renders almost no text is a block page, consent
                # wall, or JS-only shell. Reporting that as "success" invites
                # the model to summarize nothing as if it were readable content,
                # so name the problem instead.
                if len(trimmed_text.strip()) < _MIN_READABLE_TEXT_CHARS:
                    result["status"] = "partial"
                    result["message"] = (
                        f"{url} loaded but returned almost no readable text "
                        f"({len(trimmed_text.strip())} characters) - likely a bot block, "
                        "consent wall, or a page that renders only after interaction. "
                        "Use a different source or URL rather than summarizing this."
                    )
                return result
                
        except Exception as e:
            logger.error(f"Error scraping website {url}: {describe_exception(e)}")
            return {
                "url": str(url),
                "title": "",
                "text_content": "",
                "links": [],
                "media": [],
                "metadata": {},
                "status": "error",
                "error": describe_exception(e)
            }
    
    async def take_screenshot(
        self,
        url: str,
        filename: Optional[str] = None,
        headless: Optional[bool] = None,
        headed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Take a screenshot of a webpage.
        
        Args:
            url: The URL to screenshot
            filename: Optional filename to save screenshot
            headless: Optional override for headless browser mode
            headed: Optional alias to request a visible browser window
            
        Returns:
            Dictionary with screenshot information
        """
        if not is_internet_access_enabled():
            logger.info("Screenshot (method) blocked: internet access disabled")
            return {
                'status': 'disabled',
                'enabled': False,
                'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
                'transfer_to': 'autoyou_agent',
                'url': url,
                'screenshot_path': '',
                'filename': filename or '',
                'title': ''
            }
        try:
            logger.info(f"Taking screenshot of: {url}")
            requested_headless = _resolve_headless_override(headless, headed)
            
            # Generate filename if not provided
            if not filename:
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{timestamp}.png"
            
            # Ensure filename has .png extension
            if not filename.endswith('.png'):
                filename += '.png'
            
            async with get_playwright_browser(requested_headless) as page:
                await _goto_page_ready(page, url)
                
                # Take screenshot
                screenshot_path = filename
                await page.screenshot(path=screenshot_path, full_page=True)
                
                # Get page title for additional info
                title = await page.title()
                
                return {
                    "url": str(url),
                    "screenshot_path": screenshot_path,
                    "filename": filename,
                    "title": title,
                    "browser_headless": requested_headless if requested_headless is not None else PlaywrightDriverManager._resolve_default_headless_setting(),
                    "status": "success",
                    "message": f"Screenshot saved as {screenshot_path}"
                }
                
        except Exception as e:
            logger.error(f"Error taking screenshot of {url}: {describe_exception(e)}")
            return {
                "url": str(url),
                "screenshot_path": "",
                "filename": filename or "",
                "title": "",
                "status": "error",
                "error": describe_exception(e),
                "message": f"Failed to take screenshot: {describe_exception(e)}"
            }
    
    async def navigate_page(
        self,
        url: str,
        actions: List[Dict[str, Any]],
        headless: Optional[bool] = None,
        headed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Navigate a webpage and perform actions.
        
        Args:
            url: The URL to navigate to
            actions: List of actions to perform
            headless: Optional override for headless browser mode
            headed: Optional alias to request a visible browser window
            
        Returns:
            Dictionary with navigation results
        """
        if not is_internet_access_enabled():
            logger.info("Navigate (method) blocked: internet access disabled")
            return {
                'status': 'disabled',
                'enabled': False,
                'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
                'transfer_to': 'autoyou_agent',
                'url': url,
                'actions': actions,
                'results': []
            }
        normalized_actions = _normalize_navigation_actions(actions)
        if normalized_actions is None:
            return {
                "url": str(url),
                "final_url": "",
                "title": "",
                "actions_performed": 0,
                "results": [],
                "status": "error",
                "error": "Navigation actions must be a list of action objects or a JSON-encoded list.",
            }
        actions = normalized_actions
        try:
            logger.info(f"Navigating to: {url} with actions: {actions}")
            requested_headless = _resolve_headless_override(headless, headed)
            
            async with get_playwright_browser(requested_headless) as page:
                await _goto_page_ready(page, url)
                
                results = []
                
                for action in actions:
                    action_type = action.get('type', '').lower()
                    selector = action.get('selector', '')
                    value = action.get('value', '')
                    
                    try:
                        if action_type == 'click':
                            # Wait for element and click
                            await page.wait_for_selector(selector, timeout=10000)
                            await page.click(selector)
                            results.append({
                                "action": "click",
                                "selector": selector,
                                "status": "success"
                            })
                            
                        elif action_type == 'type' or action_type == 'input':
                            # Wait for element, clear and type
                            await page.wait_for_selector(selector, timeout=10000)
                            await page.fill(selector, value)
                            results.append({
                                "action": "type",
                                "selector": selector,
                                "value": value,
                                "status": "success"
                            })
                            
                        elif action_type == 'wait':
                            # Wait for element to appear
                            await page.wait_for_selector(selector, timeout=int(value) * 1000 if value else 10000)
                            results.append({
                                "action": "wait",
                                "selector": selector,
                                "status": "success"
                            })
                            
                        elif action_type == 'scroll':
                            # Scroll to element or by amount
                            if selector:
                                await page.locator(selector).scroll_into_view_if_needed()
                            else:
                                # Scroll by pixels if value provided
                                scroll_amount = int(value) if value else 500
                                await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                            results.append({
                                "action": "scroll",
                                "selector": selector,
                                "value": value,
                                "status": "success"
                            })
                            
                        elif action_type == 'get_text':
                            # Get text from element
                            await page.wait_for_selector(selector, timeout=10000)
                            text = await page.locator(selector).text_content()
                            results.append({
                                "action": "get_text",
                                "selector": selector,
                                "text": text,
                                "status": "success"
                            })
                            
                        elif action_type == 'get_attribute':
                            # Get attribute from element
                            await page.wait_for_selector(selector, timeout=10000)
                            attr_name = value or 'href'
                            attr_value = await page.locator(selector).get_attribute(attr_name)
                            results.append({
                                "action": "get_attribute",
                                "selector": selector,
                                "attribute": attr_name,
                                "value": attr_value,
                                "status": "success"
                            })
                            
                        else:
                            results.append({
                                "action": action_type,
                                "selector": selector,
                                "status": "error",
                                "error": f"Unknown action type: {action_type}"
                            })
                            
                        # Small delay between actions
                        await page.wait_for_timeout(1000)
                        
                    except Exception as action_error:
                        logger.error(f"Error performing action {action_type}: {action_error}")
                        results.append({
                            "action": action_type,
                            "selector": selector,
                            "status": "error",
                            "error": str(action_error)
                        })
                
                # Get final page state
                final_url = page.url
                title = await page.title()
                
                return {
                    "url": str(url),
                    "final_url": final_url,
                    "title": title,
                    "actions_performed": len(results),
                    "results": results,
                    "browser_headless": requested_headless if requested_headless is not None else PlaywrightDriverManager._resolve_default_headless_setting(),
                    "status": "success"
                }
                
        except Exception as e:
            logger.error(f"Error navigating page {url}: {describe_exception(e)}")
            return {
                "url": str(url),
                "final_url": "",
                "title": "",
                "actions_performed": 0,
                "results": [],
                "status": "error",
                "error": describe_exception(e)
            }

# Global internet tool instance
_internet_tool = InternetTool()

def _get_internet_tool() -> InternetTool:
    """Get the global internet tool instance."""
    return _internet_tool

# Wrapper functions for ADK integration
async def internet_search(query: str, max_results: int = 10) -> Dict[str, Any]:
    """Search Internet for information and return results with URLs, titles, and descriptions (async)."""
    if not is_internet_access_enabled():
        logger.info("Internet search (wrapper) blocked: internet access disabled")
        return {
            'status': 'disabled',
            'enabled': False,
            'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
            'transfer_to': 'autoyou_agent',
            'results': [],
            'results_count': 0,
            'query': query,
        }
    return await _get_internet_tool().internet_search(query, max_results)

async def scrape_website(
    url: str,
    extract_media: bool = False,
    headless: Optional[bool] = None,
    headed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Read a web page and return its text, links and metadata (async).

    Set extract_media=True to also return image/video/audio URLs. Leave it off
    when summarizing a page: image captions read like prose and get summarized
    in place of the actual headlines.

    Set headed=True or headless=False to keep the browser window visible.
    """
    if not is_internet_access_enabled():
        logger.info("Scrape (wrapper) blocked: internet access disabled")
        return {
            'status': 'disabled',
            'enabled': False,
            'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
            'transfer_to': 'autoyou_agent',
            'url': url,
            'title': '',
            'text_content': '',
            'links': [],
            'media': [],
            'metadata': {}
        }
    return await _get_internet_tool().scrape_website(url, extract_media, headless=headless, headed=headed)

async def take_screenshot(
    url: str,
    filename: Optional[str] = None,
    headless: Optional[bool] = None,
    headed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Take a screenshot of a webpage (async).

    Set headed=True or headless=False to keep the browser window visible.
    """
    if not is_internet_access_enabled():
        logger.info("Screenshot (wrapper) blocked: internet access disabled")
        return {
            'status': 'disabled',
            'enabled': False,
            'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
            'transfer_to': 'autoyou_agent',
            'url': url,
            'screenshot_path': '',
            'filename': filename or '',
            'title': ''
        }
    return await _get_internet_tool().take_screenshot(url, filename, headless=headless, headed=headed)

async def navigate_page(
    url: str,
    actions: List[Dict[str, Any]],
    headless: Optional[bool] = None,
    headed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Navigate and interact with a webpage by performing actions like clicking and typing (async).

    Set headed=True or headless=False to keep the browser window visible.
    """
    if not is_internet_access_enabled():
        logger.info("Navigate (wrapper) blocked: internet access disabled")
        return {
            'status': 'disabled',
            'enabled': False,
            'message': 'Internet access is disabled by admin. Transfer to autoyou_agent.',
            'transfer_to': 'autoyou_agent',
            'url': url,
            'actions': actions,
            'results': []
        }
    return await _get_internet_tool().navigate_page(url, actions, headless=headless, headed=headed)

def ingest_attachments(
    attachments: List[Dict[str, Any]],
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    intent_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Accept path-backed attachments for internet tasks (e.g., reverse image search).

    This function validates that provided attachments reference existing local paths
    that were temp-saved upstream, and returns a structured summary. It does not
    attempt to upload or decode content; downstream internet tools can consume
    these paths for web actions as needed.

    Args:
        attachments: List of attachment dicts including `path`, `filename`, and `mimetype`.
        source: Optional source label (e.g., "whatsapp", "telegram", "signal").
        user_id: Optional user identifier.
        session_id: Optional session identifier.
        message_id: Optional upstream message id.
        intent_hint: Optional hint like "reverse image search" to inform next steps.

    Returns:
        Dict summarizing validation results and counts per mimetype category.
    """
    ok_items: List[Dict[str, Any]] = []
    errors: List[str] = []
    counts: Dict[str, int] = {}

    for idx, att in enumerate(attachments or []):
        path = str(att.get("path") or "").strip().strip('"').strip("'")
        filename = att.get("filename") or os.path.basename(path)
        mimetype = str(att.get("mimetype") or "").lower()

        if not path:
            errors.append(f"attachment[{idx}] has no path; filename={filename or 'unknown'}")
            continue
        if not os.path.exists(path):
            # Attempt resolution into the temp autoyou_media directory using source/user/session
            try:
                import tempfile as _tempfile
                troot = os.path.join(_tempfile.gettempdir(), "autoyou_media")
                cands = []
                if source and user_id and session_id:
                    cands.append(os.path.join(troot, str(source), str(user_id), str(session_id), filename))
                if source and user_id:
                    cands.append(os.path.join(troot, str(source), str(user_id), filename))
                if source:
                    cands.append(os.path.join(troot, str(source), filename))
                for cand in cands:
                    c_norm = os.path.normpath(cand)
                    if os.path.exists(c_norm):
                        logger.info(
                            "InternetAgent resolved missing path via temp dir -> '%s'",
                            c_norm,
                        )
                        path = c_norm
                        break
            except Exception:
                pass
        if not os.path.exists(path):
            errors.append(f"attachment[{idx}] path not found: {path}")
            continue

        # Tally mimetype family
        family = (
            "image" if mimetype.startswith("image/") else
            "video" if mimetype.startswith("video/") else
            "audio" if mimetype.startswith("audio/") else
            "document" if mimetype in ("application/pdf", "text/plain") else
            "other"
        )
        counts[family] = counts.get(family, 0) + 1

        ok_items.append({
            "path": path,
            "filename": filename,
            "mimetype": mimetype,
        })

    summary = {
        "status": "success" if not errors else "partial",
        "validated_count": len(ok_items),
        "error_count": len(errors),
        "counts": counts,
        "source": source,
        "user_id": user_id,
        "session_id": session_id,
        "message_id": message_id,
        "intent_hint": intent_hint,
        "attachments": ok_items,
        "errors": errors,
        "next": "Use internet tools to act on validated paths (e.g., search)."
    }

    logger.info(
        "InternetAgent attachments validated: %s ok, %s errors | counts=%s | intent=%s",
        len(ok_items), len(errors), counts, intent_hint,
    )
    return summary

class _LazyFunctionTool:
    """Import ADK only when an agent actually asks for the FunctionTool."""

    def __init__(self, func):
        self._func = func
        self._tool = None

    def _resolve(self):
        if self._tool is None:
            from google.adk.tools import FunctionTool

            self._tool = FunctionTool(self._func)
        return self._tool

    def __getattr__(self, name: str):
        return getattr(self._resolve(), name)

    def __call__(self, *args, **kwargs):
        return self._resolve()(*args, **kwargs)

# ADK Function Tools. Keep these lazy so importing register_fastapi_cleanup
# during server startup does not pull the full ADK graph into local pairing.
internet_search_tool = _LazyFunctionTool(internet_search)
scrape_website_tool = _LazyFunctionTool(scrape_website)
take_screenshot_tool = _LazyFunctionTool(take_screenshot)
navigate_page_tool = _LazyFunctionTool(navigate_page)

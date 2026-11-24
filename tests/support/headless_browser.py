"""A small, hermetic Chromium driver for screen tests.

Starts the machine's own Chrome or Edge headless with a throwaway profile and no
network beyond 127.0.0.1, and speaks the DevTools protocol over one websocket.
Tests skip, rather than fail, where no such browser (or `websockets`) exists.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

CANDIDATES = (
    os.environ.get("AUTOYOU_TEST_BROWSER", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
NAMES = ("msedge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")


def find_browser() -> str | None:
    for candidate in CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Page:
    """One tab. Everything is synchronous; events that arrive meanwhile are kept in `events`."""

    def __init__(self, ws_url: str, port: int = 0, target: str = ""):
        from websockets.sync.client import connect
        self.ws = connect(ws_url, max_size=None, open_timeout=15)
        self.counter = 0
        self.events: list[dict] = []
        self.port, self.target = port, target
        for domain in ("Page", "Runtime", "Log", "DOM"):
            self.call(domain + ".enable")
        # A tab that is not in front is throttled and reports no focus; tests need a foreground, focused page.
        self.call("Page.bringToFront")
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)

    def call(self, method: str, **params):
        self.counter += 1
        ident = self.counter
        self.ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv(timeout=30))
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.events.append(message)

    def eval(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            raise RuntimeError(detail.get("exception", {}).get("description") or detail.get("text", "script error"))
        return result["result"].get("value")

    def wait(self, expression: str, timeout: float = 10.0, message: str = ""):
        deadline = time.time() + max(timeout, 20.0)  # a loaded machine is slower, never wrong
        last = None
        while time.time() < deadline:
            try:
                last = self.eval("Boolean(" + expression + ")")
            except RuntimeError as error:
                last = str(error)
            if last is True:
                return True
            time.sleep(0.05)
        try:
            state = self.eval("window.AutoYouContent && (() => { const s = AutoYouContent._state(); return JSON.stringify({view: s.view, scheduled: s.scheduled, load: s.load, walk: s.store.walkComplete, size: s.store.size(), filter: s.filter, error: s.error, offline: s.offline, hidden: document.hidden, focus: document.hasFocus(), events: (window.__events || []).slice(-8)}); })()")
        except Exception:
            state = None
        raise AssertionError(f"Timed out waiting for {message or expression}; last value: {last!r}; page errors: {self.errors()[-3:]}; screen: {state}")

    def goto(self, url: str):
        self.call("Page.navigate", url=url)
        self.wait("document.readyState === 'complete'", 15, "page load")

    def viewport(self, width: int, height: int, mobile: bool = False, scale: float = 1.0):
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=scale, mobile=mobile)

    def media(self, **features):
        self.call("Emulation.setEmulatedMedia", features=[{"name": name, "value": value} for name, value in features.items()])

    def click(self, selector: str, button: str = "left"):
        """A real mouse click, aimed only when the target is under the pointer (a page that is still laying out is waited for)."""
        probe = f"""(() => {{ const el = document.querySelector({json.dumps(selector)}); if (!el) return null;
            el.scrollIntoView({{block: 'center', inline: 'center'}}); const r = el.getBoundingClientRect();
            const x = r.left + r.width / 2, y = r.top + r.height / 2; const hit = document.elementFromPoint(x, y);
            return [x, y, !!hit && (el === hit || el.contains(hit) || hit.contains(el))]; }})()"""
        deadline = time.time() + 20
        point, previous = None, None
        while time.time() < deadline:
            point = self.eval(probe)
            # Aim only at a target that is under the pointer and has stopped moving.
            if point and point[2] and previous and abs(point[0] - previous[0]) < 0.5 and abs(point[1] - previous[1]) < 0.5:
                break
            previous = point
            time.sleep(0.12)
        if not point:
            raise AssertionError(f"Nothing matches {selector!r} to click")
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=point[0], y=point[1], button=button, clickCount=1)

    def press(self, key: str, code: str | None = None, modifiers: int = 0):
        codes = {"Enter": 13, "Escape": 27, "Tab": 9, "ArrowDown": 40, "ArrowUp": 38, "ArrowLeft": 37, "ArrowRight": 39, "Delete": 46, "Home": 36, "End": 35, " ": 32}
        base = {"key": key, "code": code or key, "modifiers": modifiers, "windowsVirtualKeyCode": codes.get(key, ord(key.upper()) if len(key) == 1 else 0)}
        text = key if len(key) == 1 else ("\r" if key == "Enter" else "")
        self.call("Input.dispatchKeyEvent", type="keyDown", text=text, **base)
        self.call("Input.dispatchKeyEvent", type="keyUp", **base)

    def type(self, text: str):
        for character in text:
            self.press(character)

    def screenshot(self, path: Path, full: bool = False):
        data = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=full)["data"]
        import base64
        Path(path).write_bytes(base64.b64decode(data))

    def errors(self) -> list[str]:
        found = []
        for event in self.events:
            if event.get("method") == "Runtime.exceptionThrown":
                found.append(event["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            elif event.get("method") == "Runtime.consoleAPICalled" and event["params"]["type"] == "error":
                found.append(" ".join(str(arg.get("value", arg.get("description", ""))) for arg in event["params"]["args"]))
            elif event.get("method") == "Log.entryAdded" and event["params"]["entry"]["level"] == "error":
                entry = event["params"]["entry"]
                found.append(entry.get("text", "") + " " + entry.get("url", ""))
        return found

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        if self.port and self.target:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/close/{self.target}", timeout=10).read()
            except Exception:
                pass


class Browser:
    def __init__(self, executable: str):
        self.profile = tempfile.mkdtemp(prefix="autoyou-browser-")
        self.process = subprocess.Popen(
            [executable, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
             "--disable-background-networking", "--mute-audio", "--hide-scrollbars", f"--user-data-dir={self.profile}",
             "--remote-debugging-port=0", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(self.profile) / "DevToolsActivePort"
        deadline = time.time() + 30
        while not port_file.exists() and time.time() < deadline:
            time.sleep(0.1)
        if not port_file.exists():
            self.close()
            raise RuntimeError("The browser did not start")
        self.port = int(port_file.read_text().splitlines()[0])
        self.pages: list[Page] = []

    def new_page(self) -> Page:
        url = f"http://127.0.0.1:{self.port}/json/new?about:blank"
        request = urllib.request.Request(url, method="PUT")
        with urllib.request.urlopen(request, timeout=15) as response:
            target = json.load(response)
        page = Page(target["webSocketDebuggerUrl"], self.port, target.get("id", ""))
        self.pages.append(page)
        return page

    def close(self):
        for page in getattr(self, "pages", []):
            page.close()
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)], capture_output=True, timeout=20)
            else:
                self.process.terminate()
                self.process.wait(timeout=10)
        except Exception:
            self.process.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


class LocalServer:
    """Runs an ASGI app on a free 127.0.0.1 port in this process."""

    def __init__(self, app):
        import uvicorn
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("The test server did not start")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)

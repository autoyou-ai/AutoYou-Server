# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-e49ab40d6e6bf6436fb2f379

"""Local macOS WebKit transport. No downloaded browser or debug server.

The Internet agent uses ephemeral pages through an authenticated SOCKS proxy;
every destination is resolved and pinned using the shared SSRF policy.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import base64
from contextlib import asynccontextmanager
import ipaddress
import json
import os
from pathlib import Path
import secrets
import struct
import sys
import uuid

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-e49ab40d6e6bf6436fb2f379"


def browser_executable() -> Path | None:
    if sys.platform != "darwin":
        return None
    from shared.macos_runtime_support import find_app_bundle_resource, is_app_store_build
    relative = "runtime/webkit/AutoYouBrowser.app/Contents/MacOS/AutoYouBrowser"
    bundled = find_app_bundle_resource(relative)
    if is_app_store_build():
        if not bundled or not bundled.is_file():
            raise RuntimeError("AutoYou's included browser is missing. Reinstall AutoYou.")
        return bundled
    # Explicit opt-in for isolated development checks; never overrides Store code.
    candidate = os.environ.get("AUTOYOU_WEBKIT_EXECUTABLE")
    return Path(candidate).resolve() if candidate else None


class BrowserTransport:
    def __init__(self, process):
        self.process = process
        # from __debug_provenance_b__ import yearly
        self.pending = {}
        self.sequence = 0
        self.url = "about:blank"
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def start(cls, executable):
        return cls(await asyncio.create_subprocess_exec(
            str(executable), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=128 * 1024 * 1024))

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if message.get("event") == "navigation":
                    self.url = message["url"]
                elif message.get("event") == "closed":
                    raise RuntimeError("Browser content process stopped")
                else:
                    future = self.pending.pop(message.get("id"), None)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(message["error"]))
                        else:
                            future.set_result(message.get("result"))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Browser closed"))
            self.pending.clear()

    async def call(self, method, params=None, timeout=120):
        if self.process.returncode is not None or self.reader.done():
            raise RuntimeError("Browser closed")
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params or {}}) + "\n").encode())
            await self.process.stdin.drain()
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def close(self):
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 5)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        await asyncio.gather(self.reader, return_exceptions=True)


class BrowserProxy:
    def __init__(self, allow_private=False):
        self.allow_private = allow_private
        self.password = secrets.token_hex(24)
        self.tasks = set()
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._connection, "127.0.0.1", 0)
        return {"port": self.server.sockets[0].getsockname()[1], "username": "autoyou", "password": self.password}

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        upstream = None
        try:
            async with asyncio.timeout(20):
                version, count = await reader.readexactly(2)
                methods = await reader.readexactly(count)
                if version != 5 or 2 not in methods:
                    writer.write(b"\x05\xff"); await writer.drain(); return
                writer.write(b"\x05\x02"); await writer.drain()
                version, count = await reader.readexactly(2)
                username = await reader.readexactly(count)
                count = (await reader.readexactly(1))[0]
                password = await reader.readexactly(count)
                if version != 1 or username != b"autoyou" or not secrets.compare_digest(password, self.password.encode()):
                    writer.write(b"\x01\x01"); await writer.drain(); return
                writer.write(b"\x01\x00"); await writer.drain()
                version, command, reserved, address_type = await reader.readexactly(4)
                if version != 5 or command != 1 or reserved != 0:
                    raise ValueError("Only TCP connections are supported")
                if address_type == 3:
                    size = (await reader.readexactly(1))[0]
                    host = (await reader.readexactly(size)).decode("ascii")
                elif address_type in (1, 4):
                    host = str(ipaddress.ip_address(await reader.readexactly(4 if address_type == 1 else 16)))
                else:
                    raise ValueError("Unsupported address")
                port = struct.unpack("!H", await reader.readexactly(2))[0]
                if not port:
                    raise ValueError("Invalid port")
                from shared.url_safety import resolve_safe_http_ip
                target = host if self.allow_private else await asyncio.to_thread(resolve_safe_http_ip, host)
                remote_reader, upstream = await asyncio.open_connection(target, port)
                writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
                await writer.drain()

            async def relay(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
                if destination.can_write_eof():
                    destination.write_eof()

            async with asyncio.TaskGroup() as group:
                group.create_task(relay(reader, upstream))
                group.create_task(relay(remote_reader, writer))
        except (OSError, ValueError, asyncio.IncompleteReadError, TimeoutError, ExceptionGroup):
            try:
                writer.write(b"\x05\x02\x00\x01" + b"\x00" * 6)
                await writer.drain()
            except OSError:
                pass
        finally:
            writer.close()
            if upstream:
                upstream.close()
            self.tasks.discard(task)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class WebKitPage:
    def __init__(self, transport):
        self.transport = transport
        self.timeout = 30000
        self.navigation_timeout = 30000

    @property
    def url(self):
        return self.transport.url

    def set_default_timeout(self, timeout):
        self.timeout = timeout

    def set_default_navigation_timeout(self, timeout):
        self.navigation_timeout = timeout

    async def evaluate(self, expression, arg=None):
        result = await self.transport.call("evaluate", {
            "body": f"try {{ const fn = ({expression}); const result = await (typeof fn === 'function' ? fn(arg) : fn); return JSON.stringify({{value: result === undefined ? null : result}}); }} catch(error) {{ return JSON.stringify({{error: String(error)}}); }}",
            "arguments": {"arg": arg}}, timeout=self.timeout / 1000 or 120)
        response = json.loads(result)
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response.get("value")

    async def goto(self, url, wait_until="load"):
        self.transport.url = await self.transport.call("goto", {"url": url}, timeout=self.navigation_timeout / 1000 or 120)

    async def content(self):
        return await self.evaluate("document.documentElement.outerHTML")

    async def title(self):
        return await self.evaluate("document.title")

    async def wait_for_timeout(self, timeout):
        await asyncio.sleep(timeout / 1000)

    async def wait_for_function(self, expression, *, timeout=None, arg=None):
        seconds = (self.timeout if timeout is None else timeout) / 1000 or 120
        async with asyncio.timeout(seconds):
            while True:
                value = await self.evaluate(expression, arg)
                if value:
                    return value
                await asyncio.sleep(0.1)

    async def wait_for_load_state(self, state="load", timeout=None):
        await self.wait_for_function("document.readyState === 'complete'" if state == "load" else "document.readyState !== 'loading'", timeout=timeout)

    def locator(self, selector):
        return WebKitLocator(self, [(selector, None)])

    async def wait_for_selector(self, selector, timeout=None):
        locator = self.locator(selector)
        await self.wait_for_function(f"() => ({locator.expression()}).some(e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden')", timeout=timeout)
        return locator

    async def click(self, selector):
        await self.locator(selector).click()

    async def fill(self, selector, value):
        await self.locator(selector).fill(value)

    async def screenshot(self, *, path, full_page=False):
        png = base64.b64decode(await self.transport.call("screenshot", {"fullPage": full_page}))
        Path(path).write_bytes(png)
        return png

    async def close(self):
        await self.transport.call("close", timeout=5)


class WebKitLocator:
    def __init__(self, page, steps):
        self.page, self.steps = page, steps

    def locator(self, selector):
        return WebKitLocator(self.page, self.steps + [(selector, None)])

    def nth(self, index):
        return WebKitLocator(self.page, self.steps[:-1] + [(self.steps[-1][0], index)])

    @property
    def first(self):
        return self.nth(0)

    def expression(self):
        # Match the CSS, :has-text and relative XPath selectors used by the
        # shared Internet tool, including nested DuckDuckGo result locators.
        return """(() => {
            let nodes = [document];
            for (let [selector, index] of STEPS) {
                nodes = nodes.flatMap(root => {
                    if (selector.startsWith('xpath=')) {
                        const result = document.evaluate(selector.slice(6), root, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                        return Array.from({length: result.snapshotLength}, (_, i) => result.snapshotItem(i));
                    }
                    const match = selector.match(/:has-text\\(("(?:[^"\\\\]|\\\\.)*"|'[^']*')\\)/);
                    let text;
                    if (match) { text = match[1].slice(1, -1); selector = selector.replace(match[0], ''); }
                    return [...root.querySelectorAll(selector || '*')].filter(e => text === undefined || e.textContent.includes(text));
                });
                if (index !== null) nodes = nodes[index] ? [nodes[index]] : [];
            }
            return nodes;
        })()""".replace("STEPS", json.dumps(self.steps))

    async def count(self):
        return await self.page.evaluate(f"({self.expression()}).length")

    async def text_content(self):
        return await self.page.evaluate(f"({self.expression()})[0]?.textContent ?? null")

    async def get_attribute(self, name):
        return await self.page.evaluate(f"({self.expression()})[0]?.getAttribute({json.dumps(name)}) ?? null")

    async def _act(self, action):
        await self.page.wait_for_function(f"({self.expression()}).length > 0")
        return await self.page.evaluate(f"() => {{ const e = ({self.expression()})[0]; e.scrollIntoView({{block:'center'}}); {action} }}")

    async def click(self):
        await self._act("e.click();")

    async def scroll_into_view_if_needed(self):
        await self._act("")

    async def fill(self, value):
        await self._act(f"""e.focus();
            if (e.isContentEditable) e.textContent = {json.dumps(value)};
            else {{
                const prototype = e instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                Object.getOwnPropertyDescriptor(prototype, 'value').set.call(e, {json.dumps(value)});
            }}
            e.dispatchEvent(new Event('input', {{bubbles:true}}));
            e.dispatchEvent(new Event('change', {{bubbles:true}}));""")


@asynccontextmanager
async def webkit_page(executable, *, headless=True, allow_private=False):
    proxy = BrowserProxy(allow_private)
    transport = await BrowserTransport.start(executable)
    try:
        await transport.call("open", {"headless": headless, "proxy": await proxy.start(), "publicNetworkOnly": not allow_private})
        yield WebKitPage(transport)
    finally:
        try:
            await transport.call("close", timeout=5)
        except (RuntimeError, OSError, TimeoutError):
            pass
        await transport.close()
        await proxy.close()


async def delete_whatsapp_profiles(auth_dir: Path, executable: Path):
    """Called only by explicit session reset, after the WhatsApp process stops."""
    files = list(auth_dir.glob("session-*/webkit-profile.json"))
    if not files:
        return
    transport = await BrowserTransport.start(executable)
    try:
        for file in files:
            profile = str(uuid.UUID(json.loads(file.read_text())["id"]))
            await transport.call("deleteProfile", {"profile": profile})
    finally:
        await transport.close()

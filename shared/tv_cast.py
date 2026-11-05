"""Temporary receiver URLs for a paired phone casting a single computer-hosted TV title."""
import asyncio
import ipaddress
import re
import secrets
import socket
import time

import aiohttp
from aiohttp import web

from autoyou_agents.tv_agent.website.backend.playback import RESOURCE

TITLE = re.compile(r"(?:stream/([a-f0-9]{24})|playback/([a-f0-9]{24})/([a-f0-9]{24})/master\.m3u8)\Z")
_PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, If-Range, If-None-Match", "Access-Control-Allow-Private-Network": "true",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges", "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


def lan_ipv4(host):
    try:
        address = ipaddress.ip_address(host)
        return any(address in network for network in _PRIVATE)
    except ValueError:
        return False


def cast_interface(bind, phone, addresses):
    if bind not in ("0.0.0.0", "::"):
        return bind if lan_ipv4(bind) else None
    if isinstance(phone, str) and lan_ipv4(phone):
        try:
            # Ask the routing table which interface reaches this phone; no datagram is sent.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect((phone, 9))
                host = route.getsockname()[0]
                if lan_ipv4(host):
                    return host
        except OSError:
            pass
    return next((host for host in addresses() if lan_ipv4(host)), None)


class TVCastLeases:
    """One revocable lease per paired session, on a media-only LAN socket."""
    def __init__(self):
        self.leases = {}
        self.lock = asyncio.Lock()

    async def release(self, session, request_id=None):
        async with self.lock:
            lease = self.leases.get(session)
            if lease and (request_id is None or lease.request_id == request_id):
                del self.leases[session]
                await lease.close()

    async def offer(self, session, request_id, source, host, upstream, context_headers=None):
        match = TITLE.fullmatch(source)
        if not match or not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", request_id) or not lan_ipv4(host):
            return {}
        async with self.lock:
            old = self.leases.get(session)
            if old and old.request_id == request_id and old.source == source and old.host == host:
                return {"url": old.url}
            if old:
                del self.leases[session]
                await old.close()
            if len(self.leases) >= 8:
                return {}
            lease = _Lease(request_id, source, match, upstream, context_headers or {}, lambda: self.release(session, request_id))
            try:
                await lease.start(host)
            except (OSError, RuntimeError):
                await lease.close()
                return {}
            self.leases[session] = lease
            return {"url": lease.url}


class _Lease:
    def __init__(self, request_id, source, match, upstream, context_headers, expire):
        self.request_id, self.source = request_id, source
        self.item = match[1] or match[2]
        self.version = match[3]
        self.upstream, self.expire = upstream, expire
        self.context_headers = context_headers
        self.token = secrets.token_urlsafe(24)
        self.last = time.monotonic()
        self.active = 0
        self.timer = None
        self.client = aiohttp.ClientSession(auto_decompress=False, trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                                            timeout=aiohttp.ClientTimeout(total=None, sock_connect=4, sock_read=45))
        app = web.Application()
        app.router.add_route("*", "/tv/" + self.token + "/{resource:.*}", self.read)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)

    async def start(self, host):
        self.host = host
        await self.runner.setup()
        await web.TCPSite(self.runner, host, 0).start()
        port = self.runner.addresses[0][1]
        self.url = f"http://{host}:{port}/tv/{self.token}/{self.source}"
        self.check_expiry()

    def check_expiry(self):
        left = 7200 - (time.monotonic() - self.last)
        if left <= 0:
            asyncio.create_task(self.expire())
        else:
            self.timer = asyncio.get_running_loop().call_later(left, self.check_expiry)

    async def close(self):
        if self.timer:
            self.timer.cancel()
        await self.client.close()
        await self.runner.cleanup()

    async def read(self, request):
        resource = request.match_info["resource"]
        prefix = f"playback/{self.item}/{self.version}/"
        if self.version:
            allowed = resource.startswith(prefix) and RESOURCE.fullmatch(resource[len(prefix):]) and not request.query
        else:
            allowed = resource == self.source and (not request.query or
                list(request.query) == ["ticket"] and len(request.query["ticket"]) <= 16000)
        if not allowed or not lan_ipv4(request.remote) or request.method not in {"GET", "HEAD", "OPTIONS"}:
            raise web.HTTPNotFound()
        if request.method == "OPTIONS":
            return web.Response(status=204, headers=CORS)
        if self.active >= 8:
            raise web.HTTPServiceUnavailable()
        self.active += 1
        try:
            headers = {h: request.headers[h] for h in ("Range", "If-Range", "If-None-Match") if h in request.headers}
            headers.update(self.context_headers)  # Only server-supplied context reaches the paired website.
            headers["Accept-Encoding"] = "identity"
            async with self.client.request(request.method, self.upstream + "/" + resource, params=request.query,
                                           headers=headers, allow_redirects=False) as source:
                if source.status not in {200, 206, 304, 416} or source.content_type == "text/html":
                    raise web.HTTPNotFound()
                out = {h: source.headers[h] for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "ETag") if h in source.headers}
                self.last = time.monotonic()
                response = web.StreamResponse(status=source.status, headers={**CORS, **out, "X-AutoYou-TV-Title": self.item})
                await response.prepare(request)
                if request.method != "HEAD":
                    async for chunk in source.content.iter_chunked(65536):
                        self.last = time.monotonic()
                        await response.write(chunk)
                return response
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise web.HTTPBadGateway()
        finally:
            self.active -= 1

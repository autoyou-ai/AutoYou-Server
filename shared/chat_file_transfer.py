"""Private, disk-backed chat files. Only paired data channels expose this RPC."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import time
import uuid
import weakref
import threading

_STORES = weakref.WeakSet()
_LOCAL_REFS = {}

PREFIX = "/_autoyou/chat-files/v1"
COMPUTER_LIMIT = 5 * 1024**3
PEER_LIMIT = 1024**3
INLINE_LIMIT = 4 * 1024**2
CHUNK_BYTES = 256 * 1024
MAX_ITEMS = 500


def filename(value):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(value or 'attachment')[:255]).strip('. ') or 'attachment'


class ChatFileStore:
    def __init__(self, root=None, *, limit=COMPUTER_LIMIT, scope=None):
        if root is None:
            parent = Path(os.environ.get('AUTOYOU_TEST_ROOT') or tempfile.gettempdir()) / 'autoyou_media' / 'chat-files'
            root = parent / uuid.uuid4().hex
        self.root = Path(root)
        self.limit = limit
        self.scope = scope or uuid.uuid4().hex
        self.items = {}
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _STORES.add(self)

    def _get(self, value):
        if not isinstance(value, dict):
            raise ValueError('Invalid file reference')
        item = self.items.get(value.get('id'))
        if item is None or not hmac.compare_digest(str(value.get('token', '')), item['token']):
            raise ValueError('This chat file is no longer available')
        return item

    @staticmethod
    def describe(item):
        return {k: item[k] for k in ('id', 'token', 'filename', 'mimetype', 'size', 'complete')}

    def request(self, method, url, body=None):
        with self._lock:
            return self._request(method, url, body)

    def _request(self, method, url, body=None):
        if not isinstance(url, str) or not url.startswith(PREFIX + '/'):
            raise ValueError('Unknown chat file request')
        action = url.removeprefix(PREFIX + '/')
        value = body or {}
        if method == 'GET' and action == 'limits':
            return {'max_bytes': self.limit, 'max_items': MAX_ITEMS, 'chunk_bytes': CHUNK_BYTES}
        if action == 'begin' and method == 'POST':
            size = value.get('size')
            if type(size) is not int or not 0 <= size <= self.limit:
                raise ValueError('The chat file exceeds the receiver size limit')
            self.items = {k:i for k,i in self.items.items() if i['path'].is_file()}
            if sum(not i['complete'] for i in self.items.values()) >= MAX_ITEMS * 2 or sum(i['size'] for i in self.items.values() if not i['complete']) + size > self.limit * 2:
                raise ValueError('Chat file storage is full')
            reserved = sum(i['size'] - i['received'] for i in self.items.values() if not i['complete'])
            if shutil.disk_usage(self.root).free - reserved < size + 64 * 1024**2:
                raise ValueError('Not enough free storage to receive this file')
            mime = value.get('mimetype', 'application/octet-stream')
            if not isinstance(mime, str) or not re.fullmatch(r'[\w.+-]+/[\w.+-]+', mime):
                raise ValueError('Invalid file content type')
            identifier = value.get('id') or uuid.uuid4().hex
            token = value.get('token') or secrets.token_hex(32)
            if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier) or not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token) or identifier in self.items:
                raise ValueError('Invalid or repeated file identifier')
            path = self.root / (identifier + '.part')
            path.touch(mode=0o600, exist_ok=False)
            item = dict(id=identifier, token=token, filename=filename(value.get('filename')),
                        mimetype=mime, size=size, path=path, received=0, chunks=set(), complete=False)
            self.items[identifier] = item
            return self.describe(item)
        if action == 'read' and value.get('id') not in self.items:
            token, path, scope = _LOCAL_REFS.get(value.get('id'), ('', None, None))
            if scope != self.scope or path is None or not hmac.compare_digest(token, str(value.get('token', ''))):
                raise ValueError('This chat file is unavailable on this link')
            offset = value.get('offset')
            if type(offset) is not int or not 0 <= offset <= path.stat().st_size: raise ValueError('Invalid file offset')
            with path.open('rb') as stream:
                stream.seek(offset); chunk = stream.read(CHUNK_BYTES)
            return {'offset': offset, 'data': base64.b64encode(chunk).decode(), 'end': offset + len(chunk) == path.stat().st_size}
        item = self._get(value)
        if action == 'remove' and method == 'POST':
            item['path'].unlink(missing_ok=True)
            self.items.pop(item['id'], None)
            return {}
        if action == 'append' and method == 'POST':
            offset = value.get('offset')
            encoded = value.get('data')
            if item['complete'] or type(offset) is not int or offset < 0 or offset % CHUNK_BYTES or offset >= item['size']:
                raise ValueError('Invalid chat file offset')
            if not isinstance(encoded, str) or len(encoded) > ((CHUNK_BYTES + 2) // 3) * 4:
                raise ValueError('Chat file chunk is too large')
            chunk = base64.b64decode(encoded, validate=True)
            expected = min(CHUNK_BYTES, item['size'] - offset)
            if len(chunk) != expected:
                raise ValueError('The chat file chunk size does not match')
            with item['path'].open('r+b') as stream:
                stream.seek(offset)
                if offset in item['chunks']:
                    if stream.read(expected) != chunk:
                        raise ValueError('A repeated chat file chunk changed')
                else:
                    stream.write(chunk)
                    item['chunks'].add(offset)
                    item['received'] += len(chunk)
            return {'offset': offset + len(chunk)}
        if action == 'finish' and method == 'POST':
            digest = value.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or item['received'] != item['size']:
                raise ValueError('The chat file is incomplete')
            with item['path'].open('rb') as stream:
                checksum = hashlib.sha256()
                while chunk := stream.read(CHUNK_BYTES): checksum.update(chunk)
                actual = checksum.hexdigest()
            if not hmac.compare_digest(actual, digest):
                raise ValueError('The chat file checksum does not match')
            item['complete'] = True
            item['chunks'].clear()
            return self.describe(item)
        if action == 'read' and method == 'POST':
            offset = value.get('offset')
            if not item['complete'] or type(offset) is not int or not 0 <= offset <= item['size']:
                raise ValueError('Invalid chat file download')
            with item['path'].open('rb') as stream:
                stream.seek(offset)
                chunk = stream.read(CHUNK_BYTES)
            return {'offset': offset, 'data': base64.b64encode(chunk).decode(), 'end': offset + len(chunk) == item['size']}
        raise ValueError('Unknown chat file operation')

    def close(self):
        with self._lock:
            for identifier, item in list(self.items.items()):
                if not item['complete']:
                    item['path'].unlink(missing_ok=True)
                    self.items.pop(identifier, None)

    def resolve(self, reference):
        item = self._get(reference)
        if not item['complete'] or not item['path'].is_file():
            raise ValueError('The chat file has not finished transferring')
        return item['path']

    def resolve_context(self, payload):
        context = payload.get('context') or []
        if not isinstance(context, list):
            return
        count = sum(len(source.get('attachments', [])) for source in context if isinstance(source, dict) and isinstance(source.get('attachments', []), list))
        if count > MAX_ITEMS:
            raise ValueError('Choose at most 500 chat attachments')
        total = 0
        for source in context:
            if not isinstance(source, dict):
                continue
            for item in source.get('attachments', []):
                if isinstance(item, dict) and isinstance(item.get('file_ref'), dict):
                    path = self.resolve(item['file_ref'])
                    size = self._get(item['file_ref'])['size']
                    total += size
                    if total > self.limit: raise ValueError('The chat attachment batch exceeds its size limit')
                    item['path'] = str(path)
                    item['size_bytes'] = size


async def upload_files(files, request, *, limit=COMPUTER_LIMIT, scope=None):
    """Four bounded requests in flight; file bytes never become one JSON string."""
    if len(files) > MAX_ITEMS or sum(Path(f['path']).stat().st_size for f in files) > limit:
        raise ValueError('The chat attachment batch exceeds its limit')
    limits = await request('GET', PREFIX + '/limits', None)
    if limits.get('max_bytes', 0) < sum(Path(f['path']).stat().st_size for f in files):
        raise ValueError('Update the receiver or choose a smaller chat attachment batch')
    refs = []
    try:
        for file in files:
            path = Path(file['path'])
            size = path.stat().st_size
            file.setdefault('transfer_id', uuid.uuid4().hex)
            file.setdefault('transfer_token', secrets.token_hex(32))
            register_files([file], scope=scope)
            ref = await request('POST', PREFIX + '/begin', {'id': file['transfer_id'], 'token': file['transfer_token'], 'filename': filename(file.get('filename') or path.name),
                                'mimetype': file.get('mimetype', 'application/octet-stream'), 'size': size})
            refs.append(ref)
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                offset = 0
                while offset < size:
                    pending = []
                    for _ in range(4):
                        chunk = stream.read(min(CHUNK_BYTES, size - offset))
                        if not chunk:
                            break
                        digest.update(chunk)
                        async def append(start, data):
                            answer = await request('POST', PREFIX + '/append', {**ref, 'offset': start, 'data': base64.b64encode(data).decode()})
                            if answer.get('offset') != start + len(data):
                                raise ValueError('The receiver did not confirm this file chunk')
                        pending.append(append(offset, chunk))
                        offset += len(chunk)
                    if not pending:
                        raise ValueError('The selected file changed during transfer')
                    results = await asyncio.gather(*pending, return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                if stream.read(1):
                    raise ValueError('The selected file grew during transfer')
            finished = await request('POST', PREFIX + '/finish', {**ref, 'sha256': digest.hexdigest()})
            if finished.get('complete') is not True:
                raise ValueError('The receiver did not confirm the completed file')
        return [{'filename': r['filename'], 'mimetype': r['mimetype'], 'size_bytes': r['size'],
                 'file_ref': {k:r[k] for k in ('id','token')}, 'meta': {'kind':'file'}} for r in refs]
    except BaseException:
        deadline = asyncio.get_running_loop().time() + 5
        for ref in refs:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0: break
            try:
                await asyncio.wait_for(request('POST', PREFIX + '/remove', ref), remaining)
            except BaseException:
                pass
        raise


def resolve_file_reference(reference):
    if isinstance(reference, dict):
        token, path, _ = _LOCAL_REFS.get(reference.get('id'), ('', None, None))
        if path is not None and path.is_file() and hmac.compare_digest(token, str(reference.get('token', ''))):
            return path
    for store in list(_STORES):
        try:
            return store.resolve(reference)
        except ValueError:
            pass
    raise ValueError('This chat file is no longer available')


def register_files(files, *, scope):
    parent = Path(os.environ.get('AUTOYOU_TEST_ROOT') or tempfile.gettempdir()) / 'autoyou_media' / 'chat-files' / 'sent'
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    refs = []
    for file in files:
        identifier = file.setdefault('transfer_id', uuid.uuid4().hex)
        token = file.setdefault('transfer_token', secrets.token_hex(32))
        source = Path(file['path'])
        target = parent / identifier
        if not target.exists():
            try: os.link(source, target)
            except OSError: shutil.copyfile(source, target)
        _LOCAL_REFS[identifier] = (token, target, scope)
        refs.append({'filename': filename(file.get('filename') or source.name), 'mimetype': file.get('mimetype', 'application/octet-stream'),
            'size_bytes': target.stat().st_size, 'file_ref': {'id': identifier, 'token': token}, 'meta': {'kind': 'file'}})
    return refs


async def fetch_file(ref, request, store):
    try: return resolve_file_reference(ref['file_ref'])
    except ValueError: pass
    value = {**ref['file_ref'], 'filename': ref['filename'], 'mimetype': ref['mimetype'], 'size': ref['size_bytes']}
    item = store.request('POST', PREFIX + '/begin', value)
    try:
        digest = hashlib.sha256()
        for offset in range(0, item['size'], CHUNK_BYTES):
            chunk = await request('POST', PREFIX + '/read', {**item, 'offset': offset})
            data = base64.b64decode(chunk['data'], validate=True)
            if chunk['offset'] != offset or len(data) != min(CHUNK_BYTES, item['size'] - offset): raise ValueError('Incomplete file download')
            store.request('POST', PREFIX + '/append', {**item, 'offset': offset, 'data': chunk['data']})
            digest.update(data)
        await asyncio.to_thread(store.request, 'POST', PREFIX + '/finish', {**item, 'sha256': digest.hexdigest()})
        return store.resolve(item)
    except BaseException:
        store.request('POST', PREFIX + '/remove', item)
        raise

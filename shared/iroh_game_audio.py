"""Hosted game loop in the admitted call's existing playback mixer."""
from __future__ import annotations

import asyncio
from shared.iroh_media import _join_owned
from shared.session_transport import SessionDenied


class NativeHostedGameAudio:
    def __init__(self, *, manager, path, check_current):
        self.manager, self.path, self.check_current = manager, path, check_current
        self._opening = self._closing = None
        self._playback_id = None
        self._closed = False
        self._failure = None

    async def _open(self):
        def play():
            self.check_current()
            try:
                return self.manager.play_audio_file(self.path, source="hosted_game", loop=True)
            except FileNotFoundError:
                raise # The canonical manager checks this before source allocation.
            except BaseException as error:
                self._failure = error
                raise
        status = await _join_owned(asyncio.create_task(asyncio.to_thread(play)))
        identity = status.get("playback_id") if isinstance(status, dict) else None
        if not isinstance(identity, str) or not 0 < len(identity) <= 64:
            self._failure = SessionDenied("hosted game playback did not transfer its generation")
            raise self._failure
        self._playback_id = identity

    async def start(self):
        if self._closed: raise SessionDenied("hosted game audio is retired")
        self.check_current()
        if self._opening is None:
            self._opening = asyncio.create_task(self._open(), name="iroh-hosted-game-audio-start")
        await _join_owned(self._opening)
        if self._closed: raise SessionDenied("hosted game audio ended during startup")
        self.check_current()

    async def close(self):
        self._closed = True
        if self._closing is None:
            self._closing = asyncio.create_task(self._close_owned(), name="iroh-hosted-game-audio-close")
        await _join_owned(self._closing)

    async def _close_owned(self):
        if self._opening is not None:
            await asyncio.gather(self._opening, return_exceptions=True)
        if self._failure is not None: raise self._failure
        if self._playback_id is not None:
            await _join_owned(asyncio.create_task(asyncio.to_thread(
                self.manager.stop_owned_playback, self._playback_id, source="hosted_game")))
            self._playback_id = None
